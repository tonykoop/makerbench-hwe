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

Bindings (constants and accessor aliases) are resolved to a fixed point per scope, so chains in
any order (``first = log.get; second = first; second("trials")``), tuple unpacking, ``+=`` and
walrus keys are followed. Beyond that, ``key_findings`` adds a conservative literal rule: *any*
string constant equal to a target (and any ``.target`` attribute) outside a dict-literal key or
docstring is a finding, so keys held in containers (``KEYS = {"rows": "trials"}``) or returned by
helpers are caught at the line that spells them. Allowlists are by (file, function, exact line).

``loaded_log_findings`` covers the case with no key at all: in a scope that names ``run_log``, a
json-loaded log (or a ``run_log`` parameter, or an alias of it) may only be passed as a call
argument or read with a constant key; ``.values()``, iteration, ``**log``, returning it etc. are
findings. ``run_log_module_findings`` requires such a module to *call* ``trial_rows``.

Not flagged: dict-literal keys that construct data, keyword names, docstrings/comments.

Known limits (not reasonable to close statically, and documented rather than skipped):
* a key whose characters exist only at runtime (read from input, the network, the environment, or
  assembled by ``chr``/``bytes`` arithmetic) cannot be resolved;
* data flowing into a callee's *differently named* parameter is not followed across calls (the
  callee is scanned on its own: it must use ``trial_rows`` or hit the key rules);
* deliberate obfuscation is out of scope: this guard is a regression net against *accidental* raw
  reads, not an adversarial sandbox. In particular, JavaScript keys split across string constants
  and chained through variables, and sensitive keys smuggled through containers or dict-key tricks
  (``KEYS = {"rows": "tri" + "als"}``-style indirection beyond the literal rules) are not chased;
* a run log loaded in a scope that never mentions ``run_log`` (e.g. a path held in a variable
  named otherwise) is not tracked as a log.
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
        """Bind constants and accessor aliases of this scope's statements, to a fixed point.

        One pass is not enough: ``first = log.get; second = first`` or ``a = "tri"; b = a + "als"``
        resolve only once the earlier binding exists, whatever order the traversal visits them in.
        """
        nodes = list(self._own_nodes(scope))
        self._converge(nodes)
        augmented = [n for n in nodes if isinstance(n, ast.AugAssign)]
        if augmented:  # ``key += "als"`` extends a value once; applying it every pass would never settle
            for node in augmented:
                self._bind_node(node)
            self._converge(nodes)

    def _converge(self, nodes: list[ast.AST]) -> None:
        for _ in range(12):
            before = ({k: set(v) for k, v in self.env.consts.items()}, dict(self.env.aliases))
            for node in nodes:
                if not isinstance(node, ast.AugAssign):
                    self._bind_node(node)
            if before == (self.env.consts, self.env.aliases):
                break

    def _bind(self, target: ast.AST, value: ast.AST | None, *, add: bool = False) -> None:
        if isinstance(target, ast.Name) and value is not None:
            values = _values(value, self.env, self.imported)
            if add:
                prior = self.env.const(target.id)
                values = {a + b for a in prior for b in values} if prior and values else set()
            if values:
                self.env.consts.setdefault(target.id, set()).update(values)
            accessor = None if add else _accessor_of(value, self.env, self.imported)
            if accessor:
                self.env.aliases[target.id] = accessor
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) == len(value.elts):
                for sub_target, sub_value in zip(target.elts, value.elts):
                    self._bind(sub_target, sub_value)

    def _bind_node(self, node: ast.AST) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._bind(target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self._bind(node.target, node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            self._bind(node.target, node.value, add=True)
        elif isinstance(node, ast.NamedExpr):
            self._bind(node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if isinstance(node.target, ast.Name):
                values = _literal_strings(node.iter, self.env, self.imported)
                if values:
                    self.env.consts.setdefault(node.target.id, set()).update(values)

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
    """Module-level string constants (resolved to a fixed point, so ``B = A + "x"`` works)."""
    visitor = _Visitor("", {}, frozenset(), False)
    visitor._bind_body(tree)
    return {name: set(values) for name, values in visitor.env.consts.items()}


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


LOG_PARAMS = {"run_log"}


def _is_json_load(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"load", "loads"}
        and _unparse(node.func.value) in {"json", "_json"}
    )


def loaded_log_findings(source: str, rel: str) -> list[Finding]:
    """Uses of a json-loaded run log (or a ``run_log`` parameter) that bypass the helpers.

    A name bound to ``json.load(s)(...)`` in a scope that names ``run_log`` (or a parameter named
    ``run_log``) may be passed as a call argument (the callee is scanned on its own) or read with a
    constant key (which the key rules then judge). Anything else (``.values()``, ``.items()``,
    iteration, ``**log``, returning it, comprehension over it) is reported, so an aggregate cannot
    reach the rows without a literal key. Flow into a callee's differently named parameter is not
    followed.
    """
    tree = ast.parse(source, filename=rel)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    findings: list[Finding] = []

    def scope_name(node: ast.AST) -> str:
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return ".".join(reversed(names)) or "<module>"

    scopes: list[ast.AST] = [tree] + [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        tainted: set[str] = set()
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = scope.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                if arg.arg in LOG_PARAMS:
                    tainted.add(arg.arg)
        own = list(_Visitor._own_nodes(scope))
        names_run_log = any(
            (isinstance(n, ast.Constant) and isinstance(n.value, str) and "run_log" in n.value)
            or (isinstance(n, ast.Name) and "run_log" in n.id)
            for n in own
        )
        for _ in range(4):
            for node in own:
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                    targets, value = [node.target], node.value  # ``log: dict = json.loads(...)``
                else:
                    continue
                if (names_run_log and _is_json_load(value)) or (
                    isinstance(value, ast.Name) and value.id in tainted
                ):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            tainted.add(target.id)
        if not tainted:
            continue
        for node in own:
            if not (isinstance(node, ast.Name) and node.id in tainted and isinstance(node.ctx, ast.Load)):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and node in parent.args:
                continue  # handed to another scanned function; its own body is judged there
            if isinstance(parent, ast.Subscript) and parent.value is node and isinstance(
                parent.slice, ast.Constant
            ):
                continue
            if (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in ACCESSORS
                and isinstance(parents.get(parent), ast.Call)
                and parents[parent].args
                and isinstance(parents[parent].args[0], ast.Constant)
            ):
                continue
            findings.append(
                Finding(rel, scope_name(node), node.lineno, "loaded run log escapes the helpers", _unparse(parent or node)[:120])
            )
    return sorted(set(findings), key=lambda f: (f.line, f.function))


def literal_findings(source: str, rel: str, targets: frozenset[str], attributes: bool) -> list[Finding]:
    """Every string constant equal to a target (and ``.target`` reads), except dict-literal keys
    and bare-expression strings (docstrings). ``text`` is the stripped source line."""
    lines = source.splitlines()
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def scope(node: ast.AST) -> str:
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return ".".join(reversed(names)) or "<module>"

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in targets:
            parent = parents.get(node)
            if (isinstance(parent, ast.Dict) and node in parent.keys) or isinstance(parent, ast.Expr):
                continue
            found.append(Finding(rel, scope(node), node.lineno, "key literal", lines[node.lineno - 1].strip()))
        elif attributes and isinstance(node, ast.Attribute) and node.attr in targets:
            found.append(Finding(rel, scope(node), node.lineno, "attribute read", lines[node.lineno - 1].strip()))
    return found


def key_findings(
    source: str,
    rel: str,
    targets: frozenset[str],
    project_constants: dict[str, dict[str, set[str]]] | None = None,
    attributes: bool = True,
) -> list[Finding]:
    """The conservative literal rule plus the engine's resolved reads (aliases, alias chains,
    built, imported and variable keys), one finding per (function, source line).
    ``text`` is always the stripped source line so allowlists are by exact line."""
    lines = source.splitlines()
    unique: dict[tuple[str, str], Finding] = {}
    for f in literal_findings(source, rel, targets, attributes):
        unique.setdefault((f.function, f.text), f)
    for f in analyze_source(source, rel, project_constants, targets, attributes):
        function = f.function.replace(".<lambda>", "")
        text = lines[f.line - 1].strip()
        unique.setdefault((function, text), Finding(rel, function, f.line, f.kind, text))
    return sorted(unique.values(), key=lambda f: (f.line, f.function))


def hits(
    source: str,
    targets: frozenset[str],
    project_constants: dict[str, dict[str, set[str]]] | None = None,
    attributes: bool = True,
) -> set[tuple[str, str]]:
    return {(f.function, f.text) for f in key_findings(source, "<mem>", targets, project_constants, attributes)}


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
        if isinstance(node, ast.Call):  # imported-but-never-called does not count
            callee = node.func
            if getattr(callee, "id", None) == "trial_rows" or getattr(callee, "attr", None) == "trial_rows":
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
    root: Path,
    source_dirs=("makerbench", "scripts", "site"),
    targets: frozenset[str] = TARGETS,
    attributes: bool = True,
) -> tuple[list[Finding], list[Finding]]:
    """(key-access findings, loaded-run-log escapes) over every ``.py`` under the dirs."""
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
        access += key_findings(source, rel, targets, constants, attributes)
        loaders += loaded_log_findings(source, rel)
    return access, loaders
