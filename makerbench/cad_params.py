"""Parameter extraction and literal rewriting for CAD sources (#788 W1).

The design workbench lets a person tweak an instrument's named parameters
without touching the code. This module finds them and writes them back:

- **OpenSCAD**: top-level Customizer-style assignments ``name = value;`` at
  brace depth 0, outside ``module`` and ``function`` bodies. A tiny tokenizer
  tracks strings, comments and bracket depth. Nothing is executed.
- **CadQuery / build123d**: module-level ``NAME = <literal>`` assignments,
  found with ``ast.parse``. Nothing is executed or imported.

A parameter is **editable** when its value is a literal: a number, ``true``/
``false``, a quoted string, or a flat vector of numbers. Any other expression
(``scale_lengths_in[variant]``, ``fret_from_nut(14)``) is **derived** and shown
read-only with its source text. Names assigned more than once are reported and
never edited (OpenSCAD keeps the last one and warns).

Metadata comes only from what the file says, never from guesses:

- group: the nearest preceding ``/* [Group] */`` (or ``# [Group]``) comment;
- doc: the ``//`` (``#``) comment directly above, or the trailing comment;
- range/options: a trailing Customizer comment ``// [min:max]``,
  ``// [min:step:max]`` or ``// [a, b, c]``; otherwise the range is
  ``None`` and the UI says "range not declared";
- unit: a name suffix (``_mm``, ``_cm``, ``_in``, ``_deg``, ``_hz``,
  ``_count``, ``_n``) or an explicit ``unit: xx`` in the trailing comment;
  otherwise ``None``, shown as "unit unknown".

``apply_parameters`` rewrites only the literal's character span, so comments
and formatting survive, and a value equal to the current one leaves its bytes
untouched (a no-op apply is byte-identical).
"""

from __future__ import annotations

import ast
import io
import json
import math
import os
import re
import tokenize
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

SCHEMA = "makerbench-cad-params-v1"

BACKENDS = ("openscad", "cadquery")

#: Name suffix -> display unit. The masters use these consistently.
UNIT_SUFFIXES = {
    "_mm": "mm",
    "_cm": "cm",
    "_in": "in",
    "_deg": "deg",
    "_hz": "Hz",
    "_count": "count",
    "_n": "count",
}

MAX_PARAMETERS = 500

_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_FULL_NUMBER_RE = re.compile(rf"^{_NUMBER_RE.pattern}$")
_IDENT_RE = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_]*")
_GROUP_RE = re.compile(r"^\s*\[\s*(.*?)\s*\]\s*$")
_UNIT_COMMENT_RE = re.compile(r"\bunit\s*[:=]\s*([A-Za-z°/%]+)")
_RANGE_RE = re.compile(r"^\s*\[\s*([^\]]*)\]\s*(.*)$")


class ParameterError(ValueError):
    """A parameter value or name the caller supplied is not acceptable."""


@dataclass(frozen=True)
class Parameter:
    name: str
    #: "number" | "bool" | "string" | "vector" | "derived"
    kind: str
    #: Parsed literal for editable parameters, ``None`` for derived ones.
    value: object
    #: The value's source text, exactly as written.
    raw: str
    #: [start, end) character offsets of ``raw`` in the source.
    span: tuple[int, int]
    line: int
    editable: bool
    #: "editable" | "derived" | "reassigned" | "special"
    state: str
    group: Optional[str] = None
    doc: Optional[str] = None
    unit: Optional[str] = None
    #: {"min": float, "max": float, "step": float | None} or None.
    range: Optional[dict] = None
    options: Optional[list] = None
    #: Honest, human-readable states the UI shows verbatim.
    notes: tuple[str, ...] = ()


@dataclass
class ParameterModel:
    backend: str
    parameters: list[Parameter] = field(default_factory=list)
    #: What the scan could not see, e.g. included files.
    limitations: list[str] = field(default_factory=list)
    schema: str = SCHEMA

    def by_name(self) -> dict[str, Parameter]:
        return {p.name: p for p in self.parameters}

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "backend": self.backend,
            "parameters": [asdict(p) for p in self.parameters],
            "limitations": list(self.limitations),
            "editable_count": sum(1 for p in self.parameters if p.editable),
        }


# --- shared helpers ----------------------------------------------------------


def _unit_for(name: str, trailing: Optional[str]) -> Optional[str]:
    if trailing:
        match = _UNIT_COMMENT_RE.search(trailing)
        if match:
            return match.group(1)
    lowered = name.lower()
    for suffix, unit in UNIT_SUFFIXES.items():
        if lowered.endswith(suffix):
            return unit
    return None


def _parse_number(text: str) -> Optional[float]:
    text = text.strip()
    if not _FULL_NUMBER_RE.match(text):
        return None
    value = float(text)
    if not math.isfinite(value):
        return None
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    return value


def _parse_customizer_comment(trailing: Optional[str]) -> tuple[Optional[dict], Optional[list], Optional[str]]:
    """``[min:max]`` / ``[min:step:max]`` / ``[a, b, c]`` -> (range, options, rest)."""

    if not trailing:
        return None, None, None
    match = _RANGE_RE.match(trailing)
    if not match:
        return None, None, trailing.strip() or None
    inner, rest = match.group(1), match.group(2).strip() or None
    if ":" in inner:
        parts = [p.strip() for p in inner.split(":")]
        nums = [_parse_number(p) for p in parts]
        if len(parts) == 2 and None not in nums:
            return {"min": nums[0], "max": nums[1], "step": None}, None, rest
        if len(parts) == 3 and None not in nums:
            return {"min": nums[0], "max": nums[2], "step": nums[1]}, None, rest
        return None, None, trailing.strip()
    if "," in inner or inner.strip():
        options: list = []
        for part in inner.split(","):
            part = part.strip()
            if not part:
                continue
            # "value:label" options
            head = part.split(":", 1)[0].strip()
            number = _parse_number(head)
            options.append(number if number is not None else head.strip('"'))
        if options:
            return None, options, rest
    return None, None, trailing.strip() or None


def _literal_notes(kind: str, unit: Optional[str], rng: Optional[dict], options: Optional[list]) -> tuple[str, ...]:
    notes = []
    if kind == "number":
        if rng is None and options is None:
            notes.append("range not declared")
        if unit is None:
            notes.append("unit unknown")
    elif kind == "vector" and unit is None:
        notes.append("unit unknown")
    return tuple(notes)


def _format_number(value) -> str:
    if isinstance(value, bool):
        raise ParameterError("a number cannot be a bool")
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ParameterError("numbers must be finite")
    text = repr(float(value))
    return text


def _validate_new_value(param: Parameter, value) -> object:
    if not param.editable:
        raise ParameterError(f"{param.name} is {param.state}, edit it in the code")
    if param.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterError(f"{param.name} needs a number")
        if not math.isfinite(value):
            raise ParameterError(f"{param.name} needs a finite number")
        if param.range and not (param.range["min"] <= value <= param.range["max"]):
            raise ParameterError(
                f"{param.name} must be between {param.range['min']} and {param.range['max']}"
            )
        if param.options and value not in param.options:
            raise ParameterError(f"{param.name} must be one of {param.options}")
        return value
    if param.kind == "bool":
        if not isinstance(value, bool):
            raise ParameterError(f"{param.name} needs true or false")
        return value
    if param.kind == "string":
        if not isinstance(value, str):
            raise ParameterError(f"{param.name} needs a string")
        if "\n" in value or "\r" in value:
            raise ParameterError(f"{param.name} must be a single line")
        if param.options and value not in param.options:
            raise ParameterError(f"{param.name} must be one of {param.options}")
        return value
    if param.kind == "vector":
        if not isinstance(value, (list, tuple)):
            raise ParameterError(f"{param.name} needs a list of numbers")
        if len(value) != len(param.value):
            raise ParameterError(
                f"{param.name} keeps its length of {len(param.value)} (got {len(value)})"
            )
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise ParameterError(f"{param.name} needs finite numbers only")
        return list(value)
    raise ParameterError(f"{param.name} cannot be edited")


def _values_equal(a, b) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


# --- OpenSCAD ----------------------------------------------------------------


@dataclass
class _Statement:
    start: int
    end: int  # index just past the terminator (';' or '}' or '>')
    kind: str  # "assign" | "module" | "function" | "include" | "other"
    name: Optional[str] = None
    value_span: Optional[tuple[int, int]] = None


def _scad_statements(src: str) -> tuple[list[_Statement], list[tuple[int, int, str]]]:
    """Split the top level into statements and collect comments (start, end, text)."""

    statements: list[_Statement] = []
    comments: list[tuple[int, int, str]] = []
    n = len(src)
    i = 0

    def skip_ws_and_comments(pos: int) -> int:
        while pos < n:
            ch = src[pos]
            if ch in " \t\r\n":
                pos += 1
            elif src.startswith("//", pos):
                end = src.find("\n", pos)
                end = n if end == -1 else end
                comments.append((pos, end, src[pos + 2 : end]))
                pos = end
            elif src.startswith("/*", pos):
                end = src.find("*/", pos + 2)
                end = n if end == -1 else end + 2
                comments.append((pos, end, src[pos + 2 : max(pos + 2, end - 2)]))
                pos = end
            else:
                break
        return pos

    def scan_to(pos: int, stop_at_semicolon: bool, stop_at_close_brace: bool) -> int:
        """Advance over code tracking strings, comments and depth; return the
        index just past the terminator at depth 0."""

        depth = 0
        while pos < n:
            ch = src[pos]
            if ch == '"':
                pos += 1
                while pos < n and src[pos] != '"':
                    pos += 2 if src[pos] == "\\" else 1
                pos += 1
                continue
            if src.startswith("//", pos):
                end = src.find("\n", pos)
                end = n if end == -1 else end
                comments.append((pos, end, src[pos + 2 : end]))
                pos = end
                continue
            if src.startswith("/*", pos):
                end = src.find("*/", pos + 2)
                end = n if end == -1 else end + 2
                comments.append((pos, end, src[pos + 2 : max(pos + 2, end - 2)]))
                pos = end
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if ch == "}" and depth == 0 and stop_at_close_brace:
                    return pos + 1
                if depth < 0:
                    depth = 0
            elif ch == ";" and depth == 0 and stop_at_semicolon:
                return pos + 1
            pos += 1
        return n

    while i < n:
        i = skip_ws_and_comments(i)
        if i >= n:
            break
        start = i
        head = src[i:]
        if re.match(r"(include|use)\s*<", head):
            end = src.find(">", i)
            end = n if end == -1 else end + 1
            statements.append(_Statement(start, end, "include", name=src[i:end]))
            i = end
            continue
        if re.match(r"function\b", head):
            end = scan_to(i, stop_at_semicolon=True, stop_at_close_brace=False)
            statements.append(_Statement(start, end, "function"))
            i = end
            continue
        if re.match(r"module\b", head):
            # module name(args) { ... }  or module name(args) child;  (rare)
            brace = src.find("{", i)
            semi = scan_to(i, stop_at_semicolon=True, stop_at_close_brace=False)
            if brace != -1 and brace < semi:
                end = scan_to(brace, stop_at_semicolon=False, stop_at_close_brace=True)
            else:
                end = semi
            statements.append(_Statement(start, end, "module"))
            i = end
            continue
        match = _IDENT_RE.match(head)
        if match:
            after = i + match.end()
            j = after
            while j < n and src[j] in " \t":
                j += 1
            if j < n and src[j] == "=" and not src.startswith("==", j):
                value_start = j + 1
                while value_start < n and src[value_start] in " \t":
                    value_start += 1
                end = scan_to(value_start, stop_at_semicolon=True, stop_at_close_brace=False)
                value_end = end - 1 if end > 0 and src[end - 1] == ";" else end
                while value_end > value_start and src[value_end - 1] in " \t\r\n":
                    value_end -= 1
                statements.append(
                    _Statement(start, end, "assign", name=match.group(0), value_span=(value_start, value_end))
                )
                i = end
                continue
        # Anything else: a module call, possibly with a { } block or a ; end.
        semi = scan_to(i, stop_at_semicolon=True, stop_at_close_brace=False)
        brace = src.find("{", i)
        if brace != -1 and brace < semi:
            end = scan_to(brace, stop_at_semicolon=False, stop_at_close_brace=True)
        else:
            end = semi
        statements.append(_Statement(start, end, "other"))
        i = max(end, i + 1)
    return statements, comments


def _scad_literal(raw: str) -> tuple[str, object]:
    text = raw.strip()
    if text in ("true", "false"):
        return "bool", text == "true"
    number = _parse_number(text)
    if number is not None:
        return "number", number
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        body = text[1:-1]
        try:
            return "string", json.loads('"' + body + '"')
        except json.JSONDecodeError:
            return "derived", None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return "derived", None
        items = [p.strip() for p in inner.split(",")]
        numbers = [_parse_number(p) for p in items]
        if all(v is not None for v in numbers):
            return "vector", numbers
    return "derived", None


def _line_of(src: str, offset: int) -> int:
    return src.count("\n", 0, offset) + 1


def extract_openscad(source: str) -> ParameterModel:
    """Top-level assignments of an OpenSCAD file, without running OpenSCAD."""

    model = ParameterModel(backend="openscad")
    statements, comments = _scad_statements(source)
    comments.sort()

    def comment_before(pos: int) -> tuple[Optional[str], Optional[str]]:
        """(group, doc) from comments that end before ``pos``."""

        group = None
        doc = None
        stmt_line = _line_of(source, pos)
        for c_start, c_end, text in comments:
            if c_end > pos:
                break
            if source.startswith("/*", c_start):
                match = _GROUP_RE.match(text)
                if match:
                    group = match.group(1)
                    doc = None
                    continue
            # A whole-line // comment directly above documents the parameter.
            # A trailing comment on the previous line belongs to that line.
            c_line = _line_of(source, c_start)
            line_start = source.rfind("\n", 0, c_start) + 1
            whole_line = not source[line_start:c_start].strip()
            if source.startswith("//", c_start) and c_line == stmt_line - 1 and whole_line:
                doc = text.strip() or None
            elif source.startswith("//", c_start):
                doc = None
        return group, doc

    def trailing_comment(end: int) -> Optional[str]:
        line_end = source.find("\n", end)
        line_end = len(source) if line_end == -1 else line_end
        for c_start, c_end, text in comments:
            if end <= c_start < line_end and source.startswith("//", c_start):
                return text
        return None

    counts: dict[str, int] = {}
    for st in statements:
        if st.kind == "assign":
            counts[st.name] = counts.get(st.name, 0) + 1
        elif st.kind == "include":
            model.limitations.append(f"included files aren't scanned: {st.name.strip()}")

    for st in statements:
        if st.kind != "assign":
            continue
        raw = source[st.value_span[0] : st.value_span[1]]
        kind, value = _scad_literal(raw)
        group, doc = comment_before(st.start)
        trailing = trailing_comment(st.end)
        rng, options, rest = _parse_customizer_comment(trailing)
        if doc is None and rest:
            doc = rest
        unit = _unit_for(st.name, trailing)
        line = _line_of(source, st.start)
        notes: list[str] = []
        state = "editable"
        editable = kind != "derived"
        if counts.get(st.name, 0) > 1:
            state = "reassigned"
            editable = False
            notes.append("assigned more than once, edit it in the code")
        elif kind == "derived":
            state = "derived"
            notes.append("derived, edit it in the code")
        elif st.name.startswith("$"):
            state = "special"
        if editable:
            notes.extend(_literal_notes(kind, unit, rng, options))
        model.parameters.append(
            Parameter(
                name=st.name,
                kind=kind,
                value=value,
                raw=raw,
                span=st.value_span,
                line=line,
                editable=editable,
                state=state,
                group=group,
                doc=doc,
                unit=unit,
                range=rng,
                options=options,
                notes=tuple(notes),
            )
        )
        if len(model.parameters) > MAX_PARAMETERS:
            model.limitations.append(f"more than {MAX_PARAMETERS} parameters; the rest were not listed")
            model.parameters = model.parameters[:MAX_PARAMETERS]
            break
    return model


def _format_scad(param: Parameter, value) -> str:
    if param.kind == "number":
        return _format_number(value)
    if param.kind == "bool":
        return "true" if value else "false"
    if param.kind == "string":
        return json.dumps(value)
    if param.kind == "vector":
        return "[" + ", ".join(_format_number(v) for v in value) + "]"
    raise ParameterError(f"{param.name} cannot be edited")


# --- CadQuery / Python -------------------------------------------------------


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _py_literal(node: ast.AST) -> tuple[str, object]:
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return "bool", v
        if isinstance(v, (int, float)) and math.isfinite(v):
            return "number", v
        if isinstance(v, str):
            return "string", v
        return "derived", None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        kind, v = _py_literal(node.operand)
        if kind == "number":
            return "number", -v if isinstance(node.op, ast.USub) else v
        return "derived", None
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        items = []
        for elt in node.elts:
            kind, v = _py_literal(elt)
            if kind != "number":
                return "derived", None
            items.append(v)
        return "vector", items
    return "derived", None


def extract_cadquery(source: str) -> ParameterModel:
    """Module-level ``NAME = <literal>`` assignments of a CadQuery script."""

    model = ParameterModel(backend="cadquery")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        model.limitations.append(f"parse limitation: {exc.msg} (line {exc.lineno})")
        return model
    line_starts = _line_starts(source)
    lines = source.splitlines()

    comment_by_line: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comment_by_line[tok.start[0]] = tok.string[1:]
    except (tokenize.TokenError, SyntaxError):
        model.limitations.append("parse limitation: comments could not be tokenized")

    def offset(lineno: int, byte_col: int) -> int:
        # ``ast`` reports ``col_offset``/``end_col_offset`` in UTF-8 *bytes*,
        # so a non-ASCII character earlier on the line would shift a
        # character-indexed slice. Translate through the line's bytes.
        start = line_starts[lineno - 1]
        end = line_starts[lineno] if lineno < len(line_starts) else len(source)
        line_bytes = source[start:end].encode("utf-8")
        return start + len(line_bytes[:byte_col].decode("utf-8"))

    def group_and_doc(lineno: int) -> tuple[Optional[str], Optional[str]]:
        group = None
        doc = None
        for ln in range(1, lineno):
            text = comment_by_line.get(ln)
            if text is None:
                if ln < len(lines) + 1 and lines[ln - 1].strip():
                    doc = None
                continue
            match = _GROUP_RE.match(text)
            if match and not lines[ln - 1].split("#", 1)[0].strip():
                group = match.group(1)
                doc = None
            elif ln == lineno - 1 and not lines[ln - 1].split("#", 1)[0].strip():
                doc = text.strip() or None
            else:
                doc = None
        return group, doc

    targets: list[tuple[str, ast.AST, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            targets.append((node.targets[0].id, node.value, node))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            targets.append((node.target.id, node.value, node))

    counts: dict[str, int] = {}
    for name, _, _ in targets:
        counts[name] = counts.get(name, 0) + 1

    for name, value_node, stmt in targets:
        if name.startswith("__"):
            continue
        kind, value = _py_literal(value_node)
        span = (
            offset(value_node.lineno, value_node.col_offset),
            offset(value_node.end_lineno, value_node.end_col_offset),
        )
        raw = source[span[0] : span[1]]
        trailing = comment_by_line.get(stmt.end_lineno)
        rng, options, rest = _parse_customizer_comment(trailing)
        group, doc = group_and_doc(stmt.lineno)
        if doc is None and rest:
            doc = rest
        unit = _unit_for(name, trailing)
        notes: list[str] = []
        state = "editable"
        editable = kind != "derived"
        if counts[name] > 1:
            state, editable = "reassigned", False
            notes.append("assigned more than once, edit it in the code")
        elif kind == "derived":
            state = "derived"
            notes.append("derived, edit it in the code")
        if editable:
            notes.extend(_literal_notes(kind, unit, rng, options))
        model.parameters.append(
            Parameter(
                name=name,
                kind=kind,
                value=value,
                raw=raw,
                span=span,
                line=stmt.lineno,
                editable=editable,
                state=state,
                group=group,
                doc=doc,
                unit=unit,
                range=rng,
                options=options,
                notes=tuple(notes),
            )
        )
        if len(model.parameters) >= MAX_PARAMETERS:
            model.limitations.append(f"more than {MAX_PARAMETERS} parameters; the rest were not listed")
            break
    return model


def _format_python(param: Parameter, value) -> str:
    if param.kind == "number":
        return _format_number(value)
    if param.kind == "bool":
        return "True" if value else "False"
    if param.kind == "string":
        return repr(value)
    if param.kind == "vector":
        inner = ", ".join(_format_number(v) for v in value)
        if param.raw.lstrip().startswith("("):
            return "(" + inner + ("," if len(value) == 1 else "") + ")"
        return "[" + inner + "]"
    raise ParameterError(f"{param.name} cannot be edited")


# --- public API --------------------------------------------------------------


MASTER_DIR_NAME = "cad"


def find_masters(instruments_root: Path) -> list[Path]:
    """Every OpenSCAD master under ``<root>/<family>/<repo>/cad/*.scad``.

    The directory name and the ``.scad`` suffix match **case-insensitively**:
    eight repos spell the directory ``CAD/``, and a case-sensitive glob would
    silently skip them. Results are de-duplicated by inode so a
    case-insensitive filesystem (NTFS under WSL) does not list a file twice,
    and sorted. Nothing here reads file contents.
    """

    root = Path(instruments_root)
    if not root.is_dir():
        raise FileNotFoundError(f"instruments root is not a directory: {root}")
    seen: set[tuple[int, int]] = set()
    found: list[Path] = []
    for family in sorted(os.scandir(root), key=lambda e: e.name):
        if not family.is_dir(follow_symlinks=False) or family.name.startswith("."):
            continue
        for repo in sorted(os.scandir(family.path), key=lambda e: e.name):
            if not repo.is_dir(follow_symlinks=False) or repo.name.startswith("."):
                continue
            for cad in os.scandir(repo.path):
                if cad.name.lower() != MASTER_DIR_NAME or not cad.is_dir():
                    continue
                for entry in sorted(os.scandir(cad.path), key=lambda e: e.name):
                    if not entry.is_file() or not entry.name.lower().endswith(".scad"):
                        continue
                    st = entry.stat()
                    key = (st.st_dev, st.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(Path(entry.path))
    return sorted(found)


def extract_parameters(source: str, backend: str) -> ParameterModel:
    if backend == "openscad":
        return extract_openscad(source)
    if backend == "cadquery":
        return extract_cadquery(source)
    raise ValueError(f"unknown backend {backend!r}; expected one of {BACKENDS}")


def apply_parameters(source: str, values: Mapping[str, object], backend: str) -> str:
    """Return ``source`` with each named literal replaced by its new value.

    Names must be editable parameters of ``source``; values are validated by
    kind, range and options. A value equal to the current one leaves the
    original text untouched, so an apply with no changes is byte-identical.
    """

    model = extract_parameters(source, backend)
    by_name = model.by_name()
    fmt = _format_scad if backend == "openscad" else _format_python
    edits: list[tuple[int, int, str]] = []
    for name, new in values.items():
        param = by_name.get(name)
        if param is None:
            raise ParameterError(f"no parameter named {name!r}")
        new = _validate_new_value(param, new)
        if _values_equal(param.value, new):
            continue
        edits.append((param.span[0], param.span[1], fmt(param, new)))
    edits.sort(reverse=True)
    out = source
    for start, end, text in edits:
        out = out[:start] + text + out[end:]
    return out


def changed_values(before: ParameterModel, after: ParameterModel) -> dict[str, list]:
    """``{name: [old, new]}`` for parameters whose value differs."""

    b, a = before.by_name(), after.by_name()
    delta: dict[str, list] = {}
    for name in sorted(set(b) | set(a)):
        old = b[name].value if name in b else None
        new = a[name].value if name in a else None
        if name not in b or name not in a or not _values_equal(old, new):
            delta[name] = [old, new]
    return delta

