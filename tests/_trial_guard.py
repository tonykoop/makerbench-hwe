"""AST guard for raw run-log trial access (#1085, follow-up to #1079's regex guard).

Scoring and publishing code must read run-log trials through ``run_log_io.trial_rows`` (which
applies track isolation). This walks the syntax tree of a module and reports every place where
the constant key ``"trials"`` is used to read, write or fetch something, however it is spelled:

* ``x["trials"]`` (load, store, delete) and ``x.get/setdefault/pop/__getitem__/__setitem__("trials")``;
* ``getattr(x, "get")("trials")`` and ``getattr(x, "trials")``;
* aliases of an accessor (``fetch = x.get; fetch("trials")``, ``fetch = getattr(x, "get")``),
  ``functools.partial(x.get, "trials")``, ``operator.itemgetter("trials")`` / ``getitem(x, "trials")``;
* keys held in variables, loop variables over literal containers, module constants (also imported
  from another scanned module), concatenation, f-strings, ``"".join`` and ``.lower()/.upper()/.strip()``
  of constants.

Not flagged: dict-literal keys that construct data, keyword names, and docstrings/comments.
``.values()``/``.items()``-style indirect reads of a loaded run log cannot be seen by a key search;
``run_log_module_findings`` closes that gap by requiring a module that loads ``run_log.json`` JSON
to use ``trial_rows`` (or to be listed in the allowlist).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

TARGETS = frozenset({"trials"})
ACCESSORS = {"get", "setdefault", "pop", "__getitem__", "__setitem__", "__delitem__"}
KEY_FUNCS = {"getattr", "itemgetter", "attrgetter", "getitem", "setitem", "delitem"}
STR_METHODS = {"lower", "upper", "strip", "casefold"}


@dataclass(frozen=True)
class Finding:
    file: str
    function: str  # enclosing qualname, or "<module>"
    line: int
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} in {self.function}: {self.kind}: {self.text}"


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "<expr>"


class _Env:
    """Flow-insensitive constant/alias bindings for one scope, chained to its parent."""

    def __init__(self, parent: "_Env | None" = None):
        self.parent = parent
        self.consts: dict[str, set[str]] = {}
        self.aliases: dict[str, str] = {}

    def const(self, name: str) -> set[str]:
        env: _Env | None = self
        while env is not None:
            if name in env.consts:
                return env.consts[name]
            env = env.parent
        return set()

    def alias(self, name: str) -> str | None:
        env: _Env | None = self
        while env is not None:
            if name in env.aliases:
                return env.aliases[name]
            env = env.parent
        return None


def _values(node: ast.AST, env: _Env, imported: dict[str, set[str]]) -> set[str]:
    """Possible string values of an expression, resolved statically (empty if unknown)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return env.const(node.id) or imported.get(node.id, set())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _values(node.left, env, imported), _values(node.right, env, imported)
        return {a + b for a in left for b in right}
    if isinstance(node, ast.JoinedStr):
        parts: list[set[str]] = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append({str(part.value)})
            elif isinstance(part, ast.FormattedValue):
                parts.append(_values(part.value, env, imported))
            else:
                return set()
        out = {""}
        for options in parts:
            if not options:
                return set()
            out = {a + b for a in out for b in options}
        return out
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in STR_METHODS and not node.args:
            return {getattr(v, node.func.attr)() for v in _values(node.func.value, env, imported)}
        if node.func.attr == "join" and len(node.args) == 1:
            seps = _values(node.func.value, env, imported)
            seq = node.args[0]
            if seps and isinstance(seq, (ast.List, ast.Tuple)):
                items = [_values(e, env, imported) for e in seq.elts]
                if all(items):
                    out = {""}
                    sep = next(iter(seps))
                    for i, options in enumerate(items):
                        out = {a + (sep if i else "") + b for a in out for b in options}
                    return out
    return set()


def _literal_strings(node: ast.AST, env: _Env, imported: dict[str, set[str]]) -> set[str]:
    """String values in a literal container (for ``for k in ("a", "trials"):``)."""
    out: set[str] = set()
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for element in node.elts:
            out |= _values(element, env, imported)
    return out


def _is_target(
    node: ast.AST | None, env: _Env, imported: dict[str, set[str]], targets: frozenset[str]
) -> bool:
    return node is not None and bool(targets & _values(node, env, imported))


def _accessor_of(node: ast.AST, env: _Env, imported: dict[str, set[str]]) -> str | None:
    """The accessor name if ``node`` evaluates to ``x.get``-like callable, directly or via alias."""
    if isinstance(node, ast.Attribute) and node.attr in ACCESSORS:
        return node.attr
    if isinstance(node, ast.Name):
        return env.alias(node.id)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    ):
        for value in _values(node.args[1], env, imported):
            if value in ACCESSORS:
                return value
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(
        self, rel: str, imported: dict[str, set[str]], targets: frozenset[str], attributes: bool
    ):
        self.targets = targets
        self.attributes = attributes
        self.rel = rel
        self.imported = imported
        self.findings: list[Finding] = []
        self.stack: list[str] = []
        self.env = _Env()

    # ----- scopes --------------------------------------------------------
    def _qual(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _flag(self, node: ast.AST, kind: str) -> None:
        self.findings.append(
            Finding(self.rel, self._qual(), getattr(node, "lineno", 0), kind, _unparse(node)[:120])
        )

    def _scoped(self, node: ast.AST, name: str) -> None:
        outer = self.env
        self.env = _Env(outer)
        self.stack.append(name)
        self._bind_body(node)
        self.generic_visit(node)
        self.stack.pop()
        self.env = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node, node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._scoped(node, "<lambda>")

    # ----- bindings (pre-pass over the scope's own statements) --------------
    def _bind_body(self, scope: ast.AST) -> None:
        for node in self._own_nodes(scope):
            if isinstance(node, ast.Assign):
                values = _values(node.value, self.env, self.imported)
                accessor = _accessor_of(node.value, self.env, self.imported)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if values:
                            self.env.consts.setdefault(target.id, set()).update(values)
                        if accessor:
                            self.env.aliases[target.id] = accessor
            elif (
                isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value
            ):
                values = _values(node.value, self.env, self.imported)
                if values:
                    self.env.consts.setdefault(node.target.id, set()).update(values)
                accessor = _accessor_of(node.value, self.env, self.imported)
                if accessor:
                    self.env.aliases[node.target.id] = accessor
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                target = node.target
                if isinstance(target, ast.Name):
                    values = _literal_strings(node.iter, self.env, self.imported)
                    if values:
                        self.env.consts.setdefault(target.id, set()).update(values)

    @staticmethod
    def _own_nodes(scope: ast.AST):
        """Nodes of ``scope`` excluding nested function/class bodies (their own scopes)."""
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            node = stack.pop()
            yield node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.extend(ast.iter_child_nodes(node))

    # ----- the checks ------------------------------------------------------
    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_target(node.slice, self.env, self.imported, self.targets):
            self._flag(node, "subscript")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.attributes and node.attr in self.targets:
            self._flag(node, "attribute read")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        accessor = _accessor_of(func, self.env, self.imported)
        if accessor and node.args and _is_target(node.args[0], self.env, self.imported, self.targets):
            self._flag(node, f"call to {accessor}")
        elif (
            isinstance(func, ast.Name)
            and func.id in KEY_FUNCS
            or (isinstance(func, ast.Attribute) and func.attr in KEY_FUNCS)
        ):
            if any(_is_target(a, self.env, self.imported, self.targets) for a in node.args):
                self._flag(node, "key function")
        elif isinstance(func, (ast.Name, ast.Attribute)) and (
            getattr(func, "id", None) == "partial" or getattr(func, "attr", None) == "partial"
        ):
            if (
                len(node.args) >= 2
                and _accessor_of(node.args[0], self.env, self.imported)
                and _is_target(node.args[1], self.env, self.imported, self.targets)
            ):
                self._flag(node, "partial accessor")
        self.generic_visit(node)


def _module_constants(tree: ast.Module) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    env = _Env()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            values = _values(node.value, env, {})
            for target in node.targets:
                if isinstance(target, ast.Name) and values:
                    out.setdefault(target.id, set()).update(values)
                    env.consts.setdefault(target.id, set()).update(values)
    return out


def analyze_source(
    source: str,
    rel: str,
    project_constants: dict[str, dict[str, set[str]]] | None = None,
    targets: frozenset[str] = TARGETS,
    attributes: bool = False,
) -> list[Finding]:
    """Findings for one module. ``project_constants`` maps module basename -> exported constants.

    ``targets`` are the key names to hunt for; ``attributes`` also flags ``x.<target>`` reads
    (for objective keys that dataclasses expose as attributes)."""
    tree = ast.parse(source, filename=rel)
    imported: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            exports = (project_constants or {}).get(node.module.split(".")[-1], {})
            for alias in node.names:
                if alias.name in exports:
                    imported[alias.asname or alias.name] = exports[alias.name]
    visitor = _Visitor(rel, imported, targets, attributes)
    visitor._bind_body(tree)  # module-level bindings
    visitor.visit(tree)
    return visitor.findings


def run_log_module_findings(source: str, rel: str) -> list[Finding]:
    """A module that json-loads a run log must route its rows through ``trial_rows``."""
    tree = ast.parse(source, filename=rel)
    loads_json = mentions_run_log = uses_helper = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"load", "loads"} and _unparse(node.func.value) in {
                "json",
                "_json",
            }:
                loads_json = True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "run_log" in node.value
        ):
            mentions_run_log = True
        if isinstance(node, ast.Name) and node.id == "trial_rows":
            uses_helper = True
        if isinstance(node, ast.alias) and node.name == "trial_rows":
            uses_helper = True
    if loads_json and mentions_run_log and not uses_helper:
        return [
            Finding(
                rel,
                "<module>",
                1,
                "run-log loader without trial_rows",
                "json-loads a run log but never uses run_log_io.trial_rows",
            )
        ]
    return []


def scan_tree(
    root: Path, source_dirs=("makerbench", "scripts", "site")
) -> tuple[list[Finding], list[Finding]]:
    """(key-access findings, loader-without-helper findings) over every ``.py`` under the dirs."""
    files = [p for d in source_dirs for p in sorted((root / d).rglob("*.py"))]
    sources = {p: p.read_text(encoding="utf-8") for p in files}
    constants: dict[str, dict[str, set[str]]] = {}
    for path, source in sources.items():
        try:
            constants[path.stem] = _module_constants(ast.parse(source))
        except SyntaxError:
            continue
    access: list[Finding] = []
    loaders: list[Finding] = []
    for path, source in sources.items():
        rel = path.relative_to(root).as_posix()
        access += analyze_source(source, rel, constants)
        loaders += run_log_module_findings(source, rel)
    return access, loaders
