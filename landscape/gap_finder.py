"""Coverage-gap finder for MakerBench-HWE (#242).

Identifies what the competitive landscape covers that MakerBench-HWE doesn't
(and vice versa), dimension by dimension.

Public surface
--------------
CoverageGap     Dataclass describing one per-dimension gap.
CoverageGapFinder  Engine that computes all gaps from a BenchmarkRegistry.

The finder is **read-only and deterministic**.  It operates on the same
docs/landscape.yaml data as the registry, so the gaps it finds are always
consistent with the machine-readable competitive landscape.

Terminology
-----------
We say MakerBench "claims" a value on a dimension when that value is non-empty
in the MakerBench-HWE entry in landscape.yaml.  A "gap" exists when at least
one *competing benchmark* (kind == "benchmark") claims a value that MakerBench
does not claim.  A "lead" exists when MakerBench claims something no benchmark
competitor claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import BenchmarkRegistry, BenchmarkEntry, AXIS_VOCAB

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAKERBENCH_NAME = "MakerBench-HWE"

# Dimensions that can be compared as sets of tokens
_SET_DIMENSIONS: list[str] = [
    "axis",
    "fab_processes",
]

# Dimensions that are single-value (grading, agentic, scope)
_SCALAR_DIMENSIONS: list[str] = [
    "grading",
    "agentic",
    "scope",
]


# ---------------------------------------------------------------------------
# CoverageGap
# ---------------------------------------------------------------------------

@dataclass
class CoverageGap:
    """One per-dimension gap between MakerBench-HWE and the competitive landscape.

    Attributes
    ----------
    dimension
        The landscape dimension (e.g. ``"axis"``, ``"fab_processes"``).
    makerbench_values
        Values claimed by MakerBench-HWE on this dimension.
    competitor_values
        Union of values claimed by benchmark-kind competitors.
    gap
        Values competitors claim that MakerBench does **not**.
        Empty → no coverage gap on this dimension.
    lead
        Values MakerBench claims that **no** competitor claims.
        Empty → no unique lead on this dimension.
    competitor_names
        Which competitor entries contributed to *competitor_values*.
    """

    dimension: str
    makerbench_values: set[str]
    competitor_values: set[str]
    gap: set[str] = field(default_factory=set)
    lead: set[str] = field(default_factory=set)
    competitor_names: list[str] = field(default_factory=list)

    def has_gap(self) -> bool:
        """True if competitors cover something MakerBench doesn't."""
        return bool(self.gap)

    def has_lead(self) -> bool:
        """True if MakerBench covers something no competitor does."""
        return bool(self.lead)

    def summary_line(self) -> str:
        """One-line human-readable summary."""
        parts = []
        if self.gap:
            parts.append(f"gap: {sorted(self.gap)}")
        if self.lead:
            parts.append(f"lead: {sorted(self.lead)}")
        if not parts:
            parts.append("no gap, no unique lead")
        return f"{self.dimension}: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# _extract helpers
# ---------------------------------------------------------------------------

def _extract_set(entry: BenchmarkEntry, dimension: str) -> set[str]:
    """Extract a set of tokens from *entry* for a set-typed dimension."""
    raw = entry.raw
    if dimension == "axis":
        raw_val = raw.get("axis", [])
    elif dimension == "fab_processes":
        raw_val = raw.get("fab_processes", [])
    else:
        raw_val = raw.get(dimension, [])

    if isinstance(raw_val, list):
        return {str(v).strip() for v in raw_val if v}
    return {str(raw_val).strip()} if raw_val else set()


def _extract_scalar(entry: BenchmarkEntry, dimension: str) -> set[str]:
    """Extract a single-token set from *entry* for a scalar dimension."""
    raw = entry.raw
    if dimension == "grading":
        return {entry.scoring_model} if entry.scoring_model else set()
    if dimension == "agentic":
        val = raw.get("agentic", "")
        return {str(val).strip()} if val else set()
    if dimension == "scope":
        val = raw.get("scope", "")
        if isinstance(val, list):
            return {str(v).strip() for v in val if v}
        return {str(val).strip()} if val else set()
    return set()


# ---------------------------------------------------------------------------
# CoverageGapFinder
# ---------------------------------------------------------------------------

class CoverageGapFinder:
    """Compute coverage gaps from a :class:`BenchmarkRegistry`.

    Parameters
    ----------
    registry
        Source registry.  Uses the default ``BenchmarkRegistry()`` when not
        supplied.
    makerbench_name
        The registry name of MakerBench-HWE.  Override in tests only.
    """

    def __init__(self,
                 registry: BenchmarkRegistry | None = None,
                 makerbench_name: str = _MAKERBENCH_NAME) -> None:
        self._registry = registry if registry is not None else BenchmarkRegistry()
        self._makerbench_name = makerbench_name

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def makerbench_entry(self) -> BenchmarkEntry | None:
        """Return the MakerBench-HWE registry entry, or None."""
        return self._registry.get(self._makerbench_name)

    def competitors(self) -> list[BenchmarkEntry]:
        """Return benchmark-kind entries that are *not* MakerBench-HWE."""
        return [
            e for e in self._registry.benchmarks()
            if e.name != self._makerbench_name
        ]

    def find_gaps(self) -> list[CoverageGap]:
        """Compute and return all per-dimension gaps.

        Returns an empty list if MakerBench-HWE is not in the registry.
        """
        mb = self.makerbench_entry()
        if mb is None:
            return []

        comps = self.competitors()
        gaps: list[CoverageGap] = []

        for dim in _SET_DIMENSIONS:
            gap = self._gap_for_set_dimension(mb, comps, dim)
            gaps.append(gap)

        for dim in _SCALAR_DIMENSIONS:
            gap = self._gap_for_scalar_dimension(mb, comps, dim)
            gaps.append(gap)

        return gaps

    def gaps_only(self) -> list[CoverageGap]:
        """Return only dimensions where competitors cover something we don't."""
        return [g for g in self.find_gaps() if g.has_gap()]

    def leads_only(self) -> list[CoverageGap]:
        """Return only dimensions where MakerBench has unique coverage."""
        return [g for g in self.find_gaps() if g.has_lead()]

    def axis_gap(self) -> CoverageGap:
        """Return the gap specifically on the ``axis`` dimension."""
        for g in self.find_gaps():
            if g.dimension == "axis":
                return g
        # No registry loaded — return empty gap
        return CoverageGap("axis", set(), set())

    # ------------------------------------------------------------------
    # Summary / report
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of all gaps."""
        mb = self.makerbench_entry()
        if mb is None:
            return "MakerBench-HWE not found in registry."

        lines = [
            "MakerBench-HWE Coverage Gap Analysis",
            f"  competitors: {len(self.competitors())} benchmark entries",
            "",
        ]
        for gap in self.find_gaps():
            lines.append("  " + gap.summary_line())

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Return gaps as a JSON-serialisable dict."""
        return {
            "makerbench": self._makerbench_name,
            "competitor_count": len(self.competitors()),
            "gaps": [
                {
                    "dimension": g.dimension,
                    "makerbench_values": sorted(g.makerbench_values),
                    "competitor_values": sorted(g.competitor_values),
                    "gap": sorted(g.gap),
                    "lead": sorted(g.lead),
                    "has_gap": g.has_gap(),
                    "has_lead": g.has_lead(),
                }
                for g in self.find_gaps()
            ],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _gap_for_set_dimension(self,
                               mb: BenchmarkEntry,
                               comps: list[BenchmarkEntry],
                               dim: str) -> CoverageGap:
        mb_vals = _extract_set(mb, dim)
        comp_vals: set[str] = set()
        names: list[str] = []
        for c in comps:
            cv = _extract_set(c, dim)
            if cv:
                comp_vals |= cv
                names.append(c.name)
        gap = comp_vals - mb_vals
        lead = mb_vals - comp_vals
        return CoverageGap(
            dimension=dim,
            makerbench_values=mb_vals,
            competitor_values=comp_vals,
            gap=gap,
            lead=lead,
            competitor_names=names,
        )

    def _gap_for_scalar_dimension(self,
                                  mb: BenchmarkEntry,
                                  comps: list[BenchmarkEntry],
                                  dim: str) -> CoverageGap:
        mb_vals = _extract_scalar(mb, dim)
        comp_vals: set[str] = set()
        names: list[str] = []
        for c in comps:
            cv = _extract_scalar(c, dim)
            if cv:
                comp_vals |= cv
                names.append(c.name)
        gap = comp_vals - mb_vals
        lead = mb_vals - comp_vals
        return CoverageGap(
            dimension=dim,
            makerbench_values=mb_vals,
            competitor_values=comp_vals,
            gap=gap,
            lead=lead,
            competitor_names=names,
        )
