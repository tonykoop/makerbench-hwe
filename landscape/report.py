"""Landscape report generator for MakerBench-HWE (#242).

Combines the registry, comparison matrix, and gap finder into a single
structured report object that can be rendered as plain text, Markdown, or JSON.

Public surface
--------------
LandscapeReport       Aggregated report (registry stats + matrix + gaps).
generate_report       Convenience function: build a full report in one call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .registry import BenchmarkRegistry
from .matrix import ComparisonMatrix, DIMENSIONS
from .gap_finder import CoverageGapFinder, CoverageGap


# ---------------------------------------------------------------------------
# LandscapeReport
# ---------------------------------------------------------------------------

@dataclass
class LandscapeReport:
    """Aggregated competitive-landscape report.

    Attributes
    ----------
    total_entries       Total entries in the registry.
    benchmark_count     Entries with kind == "benchmark".
    method_count        Entries with kind == "method".
    dataset_count       Entries with kind == "dataset".
    recent_count        Entries tagged *recent*.
    axis_coverage       Dict of axis → count of entries covering it.
    grading_mix         Dict of scoring model → count.
    gaps                Per-dimension coverage gaps.
    matrix_csv          Full comparison matrix as a CSV string.
    matrix_markdown     Full comparison matrix as a Markdown table.
    """

    total_entries: int = 0
    benchmark_count: int = 0
    method_count: int = 0
    dataset_count: int = 0
    recent_count: int = 0
    axis_coverage: dict[str, int] = field(default_factory=dict)
    grading_mix: dict[str, int] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    matrix_csv: str = ""
    matrix_markdown: str = ""

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return as a JSON-serialisable dict (excludes large matrix strings)."""
        return {
            "total_entries": self.total_entries,
            "benchmark_count": self.benchmark_count,
            "method_count": self.method_count,
            "dataset_count": self.dataset_count,
            "recent_count": self.recent_count,
            "axis_coverage": self.axis_coverage,
            "grading_mix": self.grading_mix,
            "gaps": self.gaps,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialise report (excluding matrix strings) to JSON."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_text(self) -> str:
        """Render a plain-text summary."""
        lines = [
            "MakerBench-HWE Competitive Landscape Report",
            "=" * 44,
            f"  Total entries : {self.total_entries}",
            f"  Benchmarks    : {self.benchmark_count}",
            f"  Methods       : {self.method_count}",
            f"  Datasets      : {self.dataset_count}",
            f"  Recent (90d)  : {self.recent_count}",
            "",
            "Axis coverage (all entries):",
        ]
        for ax, cnt in sorted(self.axis_coverage.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {ax:<30} {cnt}")

        lines += [
            "",
            "Grading-model distribution:",
        ]
        for model, cnt in sorted(self.grading_mix.items(), key=lambda kv: -kv[1]):
            lines += [f"  {model:<35} {cnt}"]

        lines += ["", "Coverage gaps (MakerBench-HWE vs benchmark competitors):"]
        for gap in self.gaps:
            dim = gap["dimension"]
            g = gap.get("gap", [])
            lead = gap.get("lead", [])
            if g:
                lines.append(f"  GAP   {dim}: {', '.join(sorted(g))}")
            if lead:
                lines.append(f"  LEAD  {dim}: {', '.join(sorted(lead))}")

        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Render a Markdown report."""
        lines = [
            "## MakerBench-HWE Competitive Landscape",
            "",
            f"| Metric | Count |",
            f"| --- | --- |",
            f"| Total entries | {self.total_entries} |",
            f"| Benchmarks | {self.benchmark_count} |",
            f"| Methods | {self.method_count} |",
            f"| Datasets | {self.dataset_count} |",
            f"| Recent (90-day) | {self.recent_count} |",
            "",
            "### Axis coverage",
            "",
            "| Axis | Entry count |",
            "| --- | --- |",
        ]
        for ax, cnt in sorted(self.axis_coverage.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {ax} | {cnt} |")

        lines += [
            "",
            "### Grading-model distribution",
            "",
            "| Model | Entry count |",
            "| --- | --- |",
        ]
        for model, cnt in sorted(self.grading_mix.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {model} | {cnt} |")

        lines += ["", "### Coverage gaps"]
        for gap in self.gaps:
            dim = gap["dimension"]
            g = gap.get("gap", [])
            lead = gap.get("lead", [])
            if g:
                lines.append(f"- **GAP `{dim}`**: competitors cover `{', '.join(sorted(g))}` that MakerBench-HWE does not")
            if lead:
                lines.append(f"- **LEAD `{dim}`**: MakerBench-HWE uniquely covers `{', '.join(sorted(lead))}`")

        lines += ["", "### Comparison matrix (benchmarks only)", "", self.matrix_markdown]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

def generate_report(registry: BenchmarkRegistry | None = None,
                    matrix_dimensions: list[str] | None = None) -> LandscapeReport:
    """Build a full :class:`LandscapeReport`.

    Parameters
    ----------
    registry
        Registry to use.  Defaults to loading ``docs/landscape.yaml``.
    matrix_dimensions
        Subset of :data:`~landscape.matrix.DIMENSIONS` to include in the
        matrix.  Defaults to all.

    Returns
    -------
    LandscapeReport
        Populated report.  Returns an empty report if the registry is empty
        (e.g. PyYAML not installed).
    """
    reg = registry if registry is not None else BenchmarkRegistry()
    dims = matrix_dimensions or DIMENSIONS

    entries = reg.all()
    report = LandscapeReport()
    report.total_entries = len(entries)
    report.benchmark_count = len(reg.benchmarks())
    report.method_count = len(reg.methods())
    report.dataset_count = sum(1 for e in entries if e.kind == "dataset")
    report.recent_count = len(reg.recent())

    # Axis coverage
    axis_cov: dict[str, int] = {}
    for e in entries:
        for ax in e.domain:
            axis_cov[ax] = axis_cov.get(ax, 0) + 1
    report.axis_coverage = axis_cov

    # Grading mix
    grading: dict[str, int] = {}
    for e in entries:
        model = e.scoring_model
        grading[model] = grading.get(model, 0) + 1
    report.grading_mix = grading

    # Gaps
    finder = CoverageGapFinder(registry=reg)
    report.gaps = [
        {
            "dimension": g.dimension,
            "gap": sorted(g.gap),
            "lead": sorted(g.lead),
            "has_gap": g.has_gap(),
            "has_lead": g.has_lead(),
        }
        for g in finder.find_gaps()
    ]

    # Matrix
    matrix = ComparisonMatrix(registry=reg)
    report.matrix_csv = matrix.to_csv(dimensions=dims, kinds=["benchmark"])
    report.matrix_markdown = matrix.to_markdown_table(dimensions=dims, kinds=["benchmark"])

    return report
