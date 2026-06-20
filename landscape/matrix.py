"""Comparison-matrix engine for MakerBench-HWE (#242).

Builds a structured comparison of MakerBench-HWE against adjacent benchmarks
along the dimensions that matter most to hardware-engineering evaluation:
axis coverage, scope, fab-process breadth, grading approach, agentic tier,
openness, anti-memorisation, and dataset scale.

Public surface
--------------
DIMENSIONS          Ordered list of canonical comparison dimensions.
MatrixRow           One row (one benchmark) in the comparison matrix.
ComparisonMatrix    Engine that builds the matrix from a BenchmarkRegistry.

The engine is **read-only and deterministic**: given the same registry it always
produces the same rows.  It never calls an LLM or fetches URLs.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from .registry import BenchmarkRegistry, BenchmarkEntry

# ---------------------------------------------------------------------------
# Canonical comparison dimensions
# ---------------------------------------------------------------------------

DIMENSIONS: list[str] = [
    "axis",
    "kind",
    "scope",
    "fab_processes",
    "grading",
    "agentic",
    "openness",
    "anti_memorization",
    "dataset_scale",
]

# Human-friendly column headers for CSV/text output
_HEADERS: dict[str, str] = {
    "axis": "Axis coverage",
    "kind": "Kind",
    "scope": "Scope",
    "fab_processes": "Fab processes",
    "grading": "Grading model",
    "agentic": "Agentic tier",
    "openness": "Openness",
    "anti_memorization": "Anti-memorisation",
    "dataset_scale": "Dataset scale",
}

# ---------------------------------------------------------------------------
# MatrixRow
# ---------------------------------------------------------------------------

@dataclass
class MatrixRow:
    """One row (one benchmark entry) in the comparison matrix.

    Attributes
    ----------
    name        The benchmark name.
    url         Primary source URL.
    dimensions  Mapping of dimension → value (raw field value, stringified).
    """

    name: str
    url: str
    dimensions: dict[str, Any] = field(default_factory=dict)

    def get(self, dimension: str, default: Any = "") -> Any:
        """Return the value for *dimension*, or *default*."""
        return self.dimensions.get(dimension, default)

    def compare_to(self, other: "MatrixRow", dimension: str) -> dict[str, Any]:
        """Return a small dict showing how *self* differs from *other* on *dimension*."""
        a = self.get(dimension)
        b = other.get(dimension)
        return {
            "dimension": dimension,
            "self_name": self.name,
            "self_value": a,
            "other_name": other.name,
            "other_value": b,
            "equal": a == b,
        }


# ---------------------------------------------------------------------------
# _extract helpers
# ---------------------------------------------------------------------------

def _extract(entry: BenchmarkEntry, dimension: str) -> Any:
    """Extract the value for *dimension* from *entry*."""
    raw = entry.raw

    if dimension == "axis":
        items = raw.get("axis", [])
        if isinstance(items, list):
            return ", ".join(items)
        return str(items)

    if dimension == "kind":
        return entry.kind

    if dimension == "scope":
        scope = raw.get("scope", "")
        if isinstance(scope, list):
            return ", ".join(scope)
        return str(scope)

    if dimension == "fab_processes":
        fab = raw.get("fab_processes", [])
        if isinstance(fab, list):
            return ", ".join(fab) if fab else "n/a"
        return str(fab)

    if dimension == "grading":
        return entry.scoring_model

    if dimension == "agentic":
        agentic = raw.get("agentic", "n/a")
        if agentic is None:
            return "n/a"
        return str(agentic)

    if dimension == "openness":
        op = raw.get("openness", "")
        if isinstance(op, str) and len(op) > 80:
            return op[:77] + "..."
        return str(op) if op else "unknown"

    if dimension == "anti_memorization":
        am = raw.get("anti_memorization", "")
        if isinstance(am, str) and am:
            return am[:100] + "..." if len(am) > 100 else am
        return "none claimed" if not am else str(am)

    if dimension == "dataset_scale":
        ds = raw.get("dataset_scale", "")
        return str(ds) if ds else "unspecified"

    # Fallback: try to read from raw dict directly
    val = raw.get(dimension, "")
    return str(val) if val else ""


# ---------------------------------------------------------------------------
# ComparisonMatrix
# ---------------------------------------------------------------------------

class ComparisonMatrix:
    """Builds a comparison matrix from a :class:`BenchmarkRegistry`.

    Parameters
    ----------
    registry
        Source of benchmark entries.  Uses the default ``BenchmarkRegistry()``
        (backed by ``docs/landscape.yaml``) when not supplied.

    Examples
    --------
    >>> matrix = ComparisonMatrix()
    >>> rows = matrix.build()
    >>> len(rows) >= 5
    True
    >>> rows[0].name
    'MakerBench-HWE'
    >>> matrix.makerbench_row().get("grading")
    'deterministic-geometric'
    """

    MAKERBENCH_NAME = "MakerBench-HWE"

    def __init__(self, registry: BenchmarkRegistry | None = None) -> None:
        self._registry = registry if registry is not None else BenchmarkRegistry()

    # ------------------------------------------------------------------
    # Core build
    # ------------------------------------------------------------------

    def build(self, dimensions: list[str] | None = None,
              kinds: list[str] | None = None) -> list[MatrixRow]:
        """Build and return the matrix rows.

        Parameters
        ----------
        dimensions
            Subset of :data:`DIMENSIONS` to include.  Defaults to all.
        kinds
            Filter to entries whose ``kind`` is in *kinds* (e.g. ``["benchmark"]``).
            Defaults to all kinds.
        """
        dims = dimensions if dimensions is not None else DIMENSIONS
        entries = self._registry.all()
        if kinds is not None:
            entries = [e for e in entries if e.kind in kinds]

        rows: list[MatrixRow] = []
        for entry in entries:
            row = MatrixRow(
                name=entry.name,
                url=entry.url,
                dimensions={dim: _extract(entry, dim) for dim in dims},
            )
            rows.append(row)
        return rows

    def makerbench_row(self) -> MatrixRow | None:
        """Return the MatrixRow for MakerBench-HWE, or None if absent."""
        for row in self.build():
            if row.name == self.MAKERBENCH_NAME:
                return row
        return None

    # ------------------------------------------------------------------
    # Coverage helpers
    # ------------------------------------------------------------------

    def coverage_per_dimension(self,
                               dimension: str,
                               kinds: list[str] | None = None) -> dict[str, list[str]]:
        """Return a mapping of dimension-value → list of entry names that have it.

        Useful for quickly seeing which axis values or scoring models are claimed
        by which entries.

        Parameters
        ----------
        dimension   One of :data:`DIMENSIONS`.
        kinds       Restrict to entries of these kinds (e.g. ``["benchmark"]``).
        """
        rows = self.build(dimensions=[dimension], kinds=kinds)
        result: dict[str, list[str]] = {}
        for row in rows:
            val = row.get(dimension, "")
            # axis and fab_processes are comma-joined lists — split them back
            if dimension in ("axis", "fab_processes", "scope"):
                tokens = [v.strip() for v in str(val).split(",") if v.strip()]
            else:
                tokens = [str(val)] if val else []
            for tok in tokens:
                result.setdefault(tok, []).append(row.name)
        return result

    def compare_entry_to_makerbench(self, name: str,
                                    dimensions: list[str] | None = None
                                    ) -> list[dict[str, Any]]:
        """Compare a named entry to MakerBench-HWE across *dimensions*.

        Returns a list of per-dimension comparison dicts.
        """
        dims = dimensions or DIMENSIONS
        rows = {r.name: r for r in self.build(dimensions=dims)}
        mb_row = rows.get(self.MAKERBENCH_NAME)
        target = rows.get(name)
        if mb_row is None or target is None:
            return []
        return [mb_row.compare_to(target, d) for d in dims]

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def to_csv(self, dimensions: list[str] | None = None,
               kinds: list[str] | None = None) -> str:
        """Serialize the matrix to a CSV string."""
        dims = dimensions or DIMENSIONS
        rows = self.build(dimensions=dims, kinds=kinds)

        buf = io.StringIO()
        headers = ["name", "url"] + [_HEADERS.get(d, d) for d in dims]
        writer = csv.writer(buf)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(
                [row.name, row.url] + [str(row.get(d, "")) for d in dims]
            )
        return buf.getvalue()

    def to_markdown_table(self, dimensions: list[str] | None = None,
                          kinds: list[str] | None = None) -> str:
        """Serialize the matrix to a Markdown table string."""
        dims = dimensions or DIMENSIONS
        rows = self.build(dimensions=dims, kinds=kinds)

        col_headers = ["Name"] + [_HEADERS.get(d, d) for d in dims]
        lines = [
            "| " + " | ".join(col_headers) + " |",
            "| " + " | ".join(["---"] * len(col_headers)) + " |",
        ]
        for row in rows:
            cells = [row.name] + [str(row.get(d, "")) for d in dims]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ComparisonMatrix({len(self._registry)} entries)"
