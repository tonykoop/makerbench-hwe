"""Portable deterministic component score for external CAD leaderboards.

The scorer is deliberately conservative: it never reads private oracles, never
calls an LLM/VLM judge, and never requires a CAD kernel. It checks that a
candidate exchange artifact is public-safe, reproducible, and structurally
scoreable, then returns a single weighted percentage plus per-rule evidence.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

SCHEMA_VERSION = "makerbench-core-dfm-score-v1"
PROFILE = "portable-dfm-v1"
SCORE_ALGORITHM = "weighted_pass_fraction_v1"
SUPPORTED_FORMATS = {
    ".step": "step",
    ".stp": "step",
    ".stl": "stl",
    ".obj": "obj",
    ".off": "off",
    ".scad": "openscad",
    ".svg": "svg",
    ".dxf": "dxf",
}
PRIVATE_PATH_MARKERS = (
    "private/oracles",
    "private\\oracles",
    "makerbench-oracles",
    "private/submissions",
    "private\\submissions",
    "makerbench-submissions",
)


@dataclass(frozen=True)
class RuleResult:
    """One deterministic rule contributing to the component score."""

    id: str
    category: str
    label: str
    passed: bool
    weight: float = 1.0
    detail: str = ""
    observed: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DfmScoreResult:
    """Stable Python API result returned by :func:`score_file`."""

    schema_version: str
    makerbench_core_version: str
    profile: str
    score_algorithm: str
    makerbench_dfm_score: float
    passed: bool
    input: dict[str, Any]
    rules: list[RuleResult]
    reproducibility: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["makerbench_dfm_score"] = round(float(self.makerbench_dfm_score), 1)
        return payload

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def score_file(path: str | Path, *, profile: str = PROFILE) -> DfmScoreResult:
    """Score a public exchange artifact and return a stable DFM component result.

    ``profile`` is versioned separately from the package so papers can cite both
    ``makerbench-core==X.Y.Z`` and ``portable-dfm-v1``.
    """

    if profile != PROFILE:
        raise ValueError(f"unsupported profile {profile!r}; expected {PROFILE!r}")

    artifact = Path(path)
    display_path = str(artifact)
    suffix = artifact.suffix.lower()
    fmt = SUPPORTED_FORMATS.get(suffix, "unknown")
    rules: list[RuleResult] = []

    exists = artifact.exists()
    rules.append(_rule("input.exists", "input", "Input file exists", exists, display_path))
    rules.append(
        _rule(
            "format.supported",
            "format",
            "Exchange format is supported",
            fmt != "unknown",
            fmt if fmt != "unknown" else f"unsupported extension {suffix or '<none>'}",
            {"extension": suffix},
        )
    )

    data = b""
    read_error = ""
    if exists:
        try:
            data = artifact.read_bytes()
        except OSError as exc:
            read_error = str(exc)
    rules.append(
        _rule(
            "input.readable",
            "input",
            "Input file is readable",
            exists and not read_error,
            read_error or "readable",
        )
    )
    rules.append(
        _rule(
            "input.non_empty",
            "input",
            "Input file is non-empty",
            bool(data),
            f"{len(data)} bytes",
            {"bytes": len(data)},
        )
    )

    normalized = display_path.replace("\\", "/").lower()
    public_safe = not any(
        marker.replace("\\", "/") in normalized for marker in PRIVATE_PATH_MARKERS
    )
    rules.append(
        _rule(
            "integrity.public_surface",
            "integrity",
            "Path does not point at private oracle/submission storage",
            public_safe,
            "public-safe path" if public_safe else "private storage path detected",
        )
    )

    sha256 = hashlib.sha256(data).hexdigest() if data else None
    rules.append(
        _rule(
            "reproducibility.sha256",
            "reproducibility",
            "Artifact has a reproducible SHA-256 digest",
            sha256 is not None,
            sha256 or "missing digest because file was unreadable or empty",
        )
    )

    if data and fmt != "unknown":
        rules.extend(_format_rules(fmt, data))

    total_weight = sum(rule.weight for rule in rules)
    passed_weight = sum(rule.weight for rule in rules if rule.passed)
    score = (passed_weight / total_weight * 100.0) if total_weight else 0.0
    required_pass = all(
        rule.passed
        for rule in rules
        if rule.id
        in {
            "input.exists",
            "input.readable",
            "input.non_empty",
            "format.supported",
            "integrity.public_surface",
        }
    )
    passed = required_pass and score >= 80.0
    version = _package_version()

    return DfmScoreResult(
        schema_version=SCHEMA_VERSION,
        makerbench_core_version=version,
        profile=profile,
        score_algorithm=SCORE_ALGORITHM,
        makerbench_dfm_score=round(score, 1),
        passed=passed,
        input={
            "path": display_path,
            "format": fmt,
            "bytes": len(data),
            "sha256": sha256,
        },
        rules=rules,
        reproducibility={
            "version_pin": f"makerbench-core=={version}",
            "profile": profile,
            "score_algorithm": SCORE_ALGORITHM,
            "oracle_access": False,
            "llm_judge": False,
        },
    )


def _rule(
    rule_id: str,
    category: str,
    label: str,
    passed: bool,
    detail: str,
    observed: dict[str, Any] | None = None,
    *,
    weight: float = 1.0,
) -> RuleResult:
    return RuleResult(
        id=rule_id,
        category=category,
        label=label,
        passed=bool(passed),
        weight=weight,
        detail=detail,
        observed=observed or {},
    )


def _format_rules(fmt: str, data: bytes) -> list[RuleResult]:
    if fmt == "step":
        return _step_rules(data)
    if fmt == "stl":
        return _stl_rules(data)
    if fmt == "obj":
        return _obj_rules(data)
    if fmt == "off":
        return _off_rules(data)
    if fmt == "openscad":
        return _openscad_rules(data)
    if fmt == "svg":
        return _svg_rules(data)
    if fmt == "dxf":
        return _dxf_rules(data)
    return []


def _step_rules(data: bytes) -> list[RuleResult]:
    text = _decode_text(data).upper()
    header = "HEADER;" in text
    data_section = "DATA;" in text
    terminator = "END-ISO-10303-21;" in text
    iso = "ISO-10303-21;" in text
    product = "PRODUCT(" in text or "PRODUCT_DEFINITION(" in text
    cartesian = "CARTESIAN_POINT(" in text or "ADVANCED_BREP_SHAPE_REPRESENTATION(" in text
    return [
        _rule("format.step.iso_10303", "format", "STEP ISO-10303-21 envelope", iso, _yes_no(iso)),
        _rule(
            "format.step.sections",
            "format",
            "STEP HEADER/DATA/terminator sections",
            header and data_section and terminator,
            f"HEADER={header} DATA={data_section} END={terminator}",
            {"header": header, "data": data_section, "terminator": terminator},
        ),
        _rule(
            "format.step.product_metadata",
            "format",
            "STEP product metadata is present",
            product,
            _yes_no(product),
        ),
        _rule(
            "geometry.step_shape_tokens",
            "geometry",
            "STEP carries B-rep/cartesian shape tokens",
            cartesian,
            _yes_no(cartesian),
        ),
    ]


def _stl_rules(data: bytes) -> list[RuleResult]:
    text_prefix = data[:256].decode("utf-8", errors="ignore").lower()
    ascii_stl = text_prefix.lstrip().startswith("solid") and b"facet normal" in data[:4096].lower()
    binary_stl = _binary_stl_triangle_count(data) is not None
    triangle_count = _binary_stl_triangle_count(data)
    return [
        _rule(
            "format.stl.syntax",
            "format",
            "STL syntax is ASCII or binary STL",
            ascii_stl or binary_stl,
            "ascii" if ascii_stl else ("binary" if binary_stl else "unrecognized"),
        ),
        _rule(
            "geometry.stl.triangles",
            "geometry",
            "STL contains at least one triangle",
            ascii_stl or (triangle_count or 0) > 0,
            f"triangles={triangle_count}" if triangle_count is not None else "ascii facets present",
            {"triangles": triangle_count},
        ),
    ]


def _obj_rules(data: bytes) -> list[RuleResult]:
    text = _decode_text(data)
    vertices = len(re.findall(r"(?m)^v\s+", text))
    faces = len(re.findall(r"(?m)^f\s+", text))
    return [
        _rule(
            "geometry.obj.vertices",
            "geometry",
            "OBJ contains vertices",
            vertices > 0,
            str(vertices),
        ),
        _rule("geometry.obj.faces", "geometry", "OBJ contains faces", faces > 0, str(faces)),
    ]


def _off_rules(data: bytes) -> list[RuleResult]:
    text = _decode_text(data)
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    valid_header = bool(lines) and lines[0] == "OFF"
    counts_ok = False
    counts: list[int] = []
    if len(lines) > 1:
        try:
            counts = [int(part) for part in lines[1].split()[:3]]
            counts_ok = len(counts) == 3 and counts[0] > 0 and counts[1] > 0
        except ValueError:
            counts_ok = False
    return [
        _rule(
            "format.off.header",
            "format",
            "OFF header is present",
            valid_header,
            _yes_no(valid_header),
        ),
        _rule(
            "geometry.off.counts",
            "geometry",
            "OFF declares vertices and faces",
            counts_ok,
            " ".join(str(part) for part in counts) if counts else "missing counts",
            {"counts": counts},
        ),
    ]


def _openscad_rules(data: bytes) -> list[RuleResult]:
    text = _decode_text(data)
    braces = _balanced(text, "{", "}")
    parens = _balanced(text, "(", ")")
    primitive = bool(
        re.search(
            r"\b(cube|sphere|cylinder|polyhedron|linear_extrude|rotate_extrude)\s*\(",
            text,
        )
    )
    return [
        _rule(
            "format.openscad.braces",
            "format",
            "OpenSCAD braces are balanced",
            braces,
            _yes_no(braces),
        ),
        _rule(
            "format.openscad.parens",
            "format",
            "OpenSCAD parentheses are balanced",
            parens,
            _yes_no(parens),
        ),
        _rule(
            "geometry.openscad.primitives",
            "geometry",
            "OpenSCAD uses a known solid primitive/extrusion",
            primitive,
            _yes_no(primitive),
        ),
    ]


def _svg_rules(data: bytes) -> list[RuleResult]:
    try:
        root = ET.fromstring(data)
        root_tag = root.tag.rsplit("}", 1)[-1].lower()
        closed_paths = [
            path.attrib.get("d", "").strip()
            for path in root.iter()
            if path.tag.rsplit("}", 1)[-1].lower() == "path"
        ]
        all_closed = bool(closed_paths) and all(d.upper().endswith("Z") for d in closed_paths)
        return [
            _rule("format.svg.xml", "format", "SVG parses as XML", root_tag == "svg", root_tag),
            _rule(
                "geometry.svg.closed_paths",
                "geometry",
                "SVG path elements are closed",
                all_closed,
                "closed="
                f"{sum(d.upper().endswith('Z') for d in closed_paths)} "
                f"total={len(closed_paths)}",
            ),
        ]
    except ET.ParseError as exc:
        return [_rule("format.svg.xml", "format", "SVG parses as XML", False, str(exc))]


def _dxf_rules(data: bytes) -> list[RuleResult]:
    text = _decode_text(data).upper()
    has_sections = "SECTION" in text and "ENTITIES" in text
    eof = "EOF" in text
    return [
        _rule(
            "format.dxf.sections",
            "format",
            "DXF SECTION/ENTITIES blocks",
            has_sections,
            _yes_no(has_sections),
        ),
        _rule("format.dxf.eof", "format", "DXF EOF marker", eof, _yes_no(eof)),
    ]


def _decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def _binary_stl_triangle_count(data: bytes) -> int | None:
    if len(data) < 84:
        return None
    count = int.from_bytes(data[80:84], "little")
    if len(data) == 84 + count * 50:
        return count
    return None


def _balanced(text: str, opener: str, closer: str) -> bool:
    depth = 0
    for char in text:
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def _yes_no(value: bool) -> str:
    return "present" if value else "missing"


def _package_version() -> str:
    for package_name in ("makerbench-core", "makerbench"):
        try:
            return importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "0.1.0"
