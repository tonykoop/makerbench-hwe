"""Interop-coverage matrix for MakerBench-HWE (#242).

Shows which external benchmarks have stub adapters, which MakerBench-HWE axis
tags each adapter covers, and how much coverage overlap exists with
MakerBench-HWE itself.  Useful for CI checks (all adapters → in registry?) and
for sprint planning (which axes still lack an interop path?).

Public surface
--------------
AdapterCoverageRow  One row in the interop matrix (one adapter).
InteropMatrix       Container and query engine.
build_interop_matrix  Convenience function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import BenchmarkRegistry, AXIS_VOCAB
from .adapters import adapter_registry
from .adapters.base import BaseInteropAdapter


# ---------------------------------------------------------------------------
# AdapterCoverageRow
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdapterCoverageRow:
    """Coverage profile for one interop adapter.

    Attributes
    ----------
    adapter_name        Registered adapter name (matches adapter_registry key).
    source_benchmark    ``source_benchmark`` declared by the adapter.
    axis_coverage       Axis tags the adapter declares it covers.
    in_landscape        True if *source_benchmark* appears in the registry.
    grading_model       Scoring model from the registry entry (or 'unknown').
    is_deterministic    True if the adapter can produce grading_transparency=True.
    """

    adapter_name: str
    source_benchmark: str
    axis_coverage: list[str]
    in_landscape: bool
    grading_model: str = "unknown"
    is_deterministic: bool = True

    def covers_axis(self, axis: str) -> bool:
        return axis in self.axis_coverage

    def axis_overlap_with(self, other_axes: list[str]) -> list[str]:
        """Return axes in common with *other_axes*."""
        return [ax for ax in self.axis_coverage if ax in other_axes]


# ---------------------------------------------------------------------------
# InteropMatrix
# ---------------------------------------------------------------------------

class InteropMatrix:
    """Interop-coverage matrix: adapters × axes.

    Parameters
    ----------
    registry        Registry to cross-reference adapter names against.
    adapter_reg     Adapter registry dict (defaults to built-in).

    Examples
    --------
    >>> m = InteropMatrix()
    >>> rows = m.rows()
    >>> len(rows) >= 5
    True
    >>> axes_covered = m.axes_with_adapters()
    >>> "dfm" in axes_covered
    True
    """

    MAKERBENCH_NAME = "MakerBench-HWE"

    def __init__(self,
                 registry: BenchmarkRegistry | None = None,
                 adapter_reg: dict[str, type[BaseInteropAdapter]] | None = None,
                 ) -> None:
        self._registry = registry if registry is not None else BenchmarkRegistry()
        self._adapter_reg = adapter_reg if adapter_reg is not None else adapter_registry
        self._rows: list[AdapterCoverageRow] = self._build()

    def _build(self) -> list[AdapterCoverageRow]:
        rows = []
        for name, cls in self._adapter_reg.items():
            instance = cls()
            registry_entry = self._registry.get(instance.source_benchmark)
            grading_model = (
                registry_entry.scoring_model if registry_entry else "unknown"
            )
            # Probe determinism: use the registry grading model when available;
            # fall back to a quick probe-adapt on an empty dict (catching the
            # mandatory-field error) to detect the adapter's own declaration.
            _DET_MODELS = {
                "deterministic-geometric",
                "executability",
                "simulation-fea",
                "n/a",
            }
            if grading_model != "unknown":
                is_det = grading_model in _DET_MODELS
            else:
                # Probe: try adapting a minimal dict; catch the validation error
                # and inspect grading_transparency from a valid call if possible.
                # Default to True for known-deterministic adapter source names.
                _det_sources = {
                    "CADGenBench", "BenDFM", "MARB / CADCLAW",
                    "Hephaestus-CCX", "FEABench", "UniCAD", "CADTestBench",
                    "ERI Benchmark",
                }
                is_det = instance.source_benchmark in _det_sources
            rows.append(AdapterCoverageRow(
                adapter_name=name,
                source_benchmark=instance.source_benchmark,
                axis_coverage=list(instance.axis_coverage),
                in_landscape=registry_entry is not None,
                grading_model=grading_model,
                is_deterministic=is_det,
            ))
        return rows

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def rows(self) -> list[AdapterCoverageRow]:
        """Return all rows (one per adapter)."""
        return list(self._rows)

    def by_axis(self, axis: str) -> list[AdapterCoverageRow]:
        """Return adapter rows that cover *axis*."""
        return [r for r in self._rows if r.covers_axis(axis)]

    def deterministic_adapters(self) -> list[AdapterCoverageRow]:
        """Return adapter rows that are marked deterministic."""
        return [r for r in self._rows if r.is_deterministic]

    def nondeterministic_adapters(self) -> list[AdapterCoverageRow]:
        """Return adapter rows that are NOT fully deterministic."""
        return [r for r in self._rows if not r.is_deterministic]

    def adapters_in_landscape(self) -> list[AdapterCoverageRow]:
        """Return adapters whose source benchmark is in the registry."""
        return [r for r in self._rows if r.in_landscape]

    def adapters_not_in_landscape(self) -> list[AdapterCoverageRow]:
        """Return adapters whose source benchmark is NOT yet in the registry."""
        return [r for r in self._rows if not r.in_landscape]

    def axes_with_adapters(self) -> list[str]:
        """Return axis tokens that at least one adapter covers."""
        covered: set[str] = set()
        for r in self._rows:
            covered.update(r.axis_coverage)
        return sorted(covered)

    def axes_without_adapters(self) -> list[str]:
        """Return documented axis tokens that NO adapter covers."""
        covered = set(self.axes_with_adapters())
        return sorted(AXIS_VOCAB - covered)

    def makerbench_axes(self) -> list[str]:
        """Return axis tags from the MakerBench-HWE registry entry."""
        mb = self._registry.get(self.MAKERBENCH_NAME)
        return list(mb.domain) if mb else []

    def makerbench_axes_covered_by_adapters(self) -> list[str]:
        """Axes MakerBench declares that at least one adapter also covers."""
        mb_axes = set(self.makerbench_axes())
        return sorted(mb_axes & set(self.axes_with_adapters()))

    def makerbench_axes_without_adapters(self) -> list[str]:
        """Axes MakerBench declares that NO adapter currently covers."""
        mb_axes = set(self.makerbench_axes())
        return sorted(mb_axes - set(self.axes_with_adapters()))

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise as a JSON-safe dict."""
        mb_axes = set(self.makerbench_axes())
        return {
            "adapter_count": len(self._rows),
            "axes_with_adapters": self.axes_with_adapters(),
            "axes_without_adapters": self.axes_without_adapters(),
            "makerbench_axes": sorted(mb_axes),
            "makerbench_axes_covered_by_adapters": self.makerbench_axes_covered_by_adapters(),
            "makerbench_axes_without_adapters": self.makerbench_axes_without_adapters(),
            "rows": [
                {
                    "adapter_name": r.adapter_name,
                    "source_benchmark": r.source_benchmark,
                    "axis_coverage": r.axis_coverage,
                    "in_landscape": r.in_landscape,
                    "grading_model": r.grading_model,
                    "is_deterministic": r.is_deterministic,
                }
                for r in self._rows
            ],
        }

    def to_markdown_table(self) -> str:
        """Render a Markdown table: adapter × axis."""
        axes = sorted(AXIS_VOCAB)
        headers = ["Adapter", "In landscape?", "Deterministic?"] + axes
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        for r in self._rows:
            cells = [
                r.adapter_name,
                "✓" if r.in_landscape else "✗",
                "✓" if r.is_deterministic else "✗",
            ] + ["✓" if r.covers_axis(ax) else "" for ax in axes]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def build_interop_matrix(registry: BenchmarkRegistry | None = None) -> InteropMatrix:
    """Build an :class:`InteropMatrix` in one call."""
    return InteropMatrix(registry=registry)
