"""Manufacturing bridge: map a design to fabrication cost across processes.

The bridge accepts a lightweight :class:`DesignSpec` (no CAD kernel required)
and returns a :class:`BridgeResult` that ranks every candidate process+profile
by estimated cost.  It wraps the deterministic adapters in
``makerbench.costing`` so the bridge is offline-safe and never contacts a
vendor API.

Story coverage: #82 (quote bridge), #81 (CostingAdapter), #240 (epic).

Typical usage::

    spec = DesignSpec(
        name="motor-mount",
        dfm_score=91.5,
        material_volume_mm3=8_000,
        removed_volume_mm3=2_500,
        hole_count=6,
    )
    bridge = ManufacturingBridge()
    result = bridge.evaluate(spec)
    print(result.cheapest().profile_id, result.cheapest().total_usd)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from makerbench.costing import (
    GeometryCostMetrics,
    ManufacturingCostEstimate,
    estimate_cost,
    get_profile,
    list_profiles,
)


# ---------------------------------------------------------------------------
# Design spec (lightweight — no CAD kernel)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignSpec:
    """Lightweight design descriptor for process-selection and multi-process costing.

    Only populate the fields relevant to the candidate manufacturing processes;
    leave others at their defaults.  This object is intentionally a thin
    wrapper around :class:`makerbench.costing.GeometryCostMetrics` with an
    additional :attr:`dfm_score` field for bridge-level filtering.
    """

    name: str
    dfm_score: float = 0.0
    material_volume_mm3: float = 0.0
    removed_volume_mm3: float = 0.0
    sheet_area_mm2: float = 0.0
    cut_length_mm: float = 0.0
    bend_count: int = 0
    hole_count: int = 0
    setup_count: int = 1
    tool_change_count: int = 0
    sheet_nesting_yield: float = 1.0
    print_time_minutes: float = 0.0
    support_material_volume_mm3: float = 0.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("DesignSpec.name must be non-empty")
        if not (0.0 <= self.dfm_score <= 100.0):
            raise ValueError(f"dfm_score must be in [0, 100], got {self.dfm_score}")

    def to_geometry_metrics(self) -> GeometryCostMetrics:
        """Convert to the costing adapter's input type."""
        return GeometryCostMetrics(
            material_volume_mm3=self.material_volume_mm3,
            removed_volume_mm3=self.removed_volume_mm3,
            cut_length_mm=self.cut_length_mm,
            bend_count=self.bend_count,
            hole_count=self.hole_count,
            setup_count=self.setup_count,
            tool_change_count=self.tool_change_count,
            sheet_area_mm2=self.sheet_area_mm2,
            sheet_nesting_yield=self.sheet_nesting_yield,
            print_time_minutes=self.print_time_minutes,
            support_material_volume_mm3=self.support_material_volume_mm3,
        )


# ---------------------------------------------------------------------------
# Per-process cost result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessCostResult:
    """Cost estimate for one process+profile pairing.

    ``rank`` is 1-based (1 = cheapest) and populated by :class:`BridgeResult`.
    """

    profile_id: str
    process_id: str
    material_id: str
    total_usd: float
    estimate: ManufacturingCostEstimate
    rank: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "process_id": self.process_id,
            "material_id": self.material_id,
            "total_usd": self.total_usd,
            "rank": self.rank,
            "estimate": self.estimate.as_dict(),
        }


# ---------------------------------------------------------------------------
# Bridge result
# ---------------------------------------------------------------------------


@dataclass
class BridgeResult:
    """Cost comparison across all evaluated process+profile pairings for one design.

    Results are sorted and ranked by ascending ``total_usd`` on construction.
    """

    design_name: str
    results: list[ProcessCostResult]

    def __post_init__(self) -> None:
        sorted_by_cost = sorted(self.results, key=lambda r: r.total_usd)
        self.results = [
            ProcessCostResult(
                profile_id=r.profile_id,
                process_id=r.process_id,
                material_id=r.material_id,
                total_usd=r.total_usd,
                estimate=r.estimate,
                rank=i + 1,
            )
            for i, r in enumerate(sorted_by_cost)
        ]

    def cheapest(self) -> ProcessCostResult | None:
        """The lowest-cost process result, or ``None`` if no profiles matched."""
        return self.results[0] if self.results else None

    def by_process(self, process_id: str) -> list[ProcessCostResult]:
        """Filter results to a specific manufacturing process."""
        return [r for r in self.results if r.process_id == process_id]

    def as_dict(self) -> dict[str, Any]:
        cheapest = self.cheapest()
        return {
            "design_name": self.design_name,
            "process_count": len(self.results),
            "cheapest_profile_id": cheapest.profile_id if cheapest else None,
            "cheapest_total_usd": cheapest.total_usd if cheapest else None,
            "results": [r.as_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Manufacturing bridge
# ---------------------------------------------------------------------------


class ManufacturingBridge:
    """Maps a :class:`DesignSpec` to cost estimates across candidate processes.

    By default the bridge evaluates all preset rate profiles in
    ``makerbench.costing.PROFILE_PRESETS``.  Pass ``profile_ids`` to restrict
    evaluation to a specific subset (e.g. only CNC profiles for a machined
    part).

    Profiles that are incompatible with the design's geometry (e.g. a CNC
    profile applied to a design with no removed volume — which would result in
    a zero machining cost but trigger missing-rate errors) are silently skipped
    so the bridge never raises on partial geometry data.
    """

    def __init__(self, profile_ids: list[str] | None = None) -> None:
        self._profile_ids: list[str] = profile_ids if profile_ids is not None else list_profiles()

    @property
    def profile_ids(self) -> list[str]:
        return list(self._profile_ids)

    def evaluate(self, design: DesignSpec) -> BridgeResult:
        """Estimate cost for ``design`` across all configured process profiles.

        Returns a :class:`BridgeResult` with results ranked cheapest-first.
        Profiles that raise :class:`ValueError` (e.g. missing rate for a
        required geometry dimension) are silently skipped.
        """
        metrics = design.to_geometry_metrics()
        results: list[ProcessCostResult] = []
        for pid in self._profile_ids:
            try:
                profile = get_profile(pid)
                estimate = estimate_cost(metrics, profile)
                results.append(ProcessCostResult(
                    profile_id=pid,
                    process_id=profile.process_id,
                    material_id=profile.material_id,
                    total_usd=estimate.total_usd,
                    estimate=estimate,
                ))
            except (ValueError, ZeroDivisionError, TypeError):
                # Profile requires geometry dimensions not present in this spec;
                # skip rather than surfacing a confusing adapter-specific error.
                pass
        return BridgeResult(design_name=design.name, results=results)

    def cheapest_process(self, design: DesignSpec) -> ProcessCostResult | None:
        """Shortcut: evaluate and return only the lowest-cost process result."""
        return self.evaluate(design).cheapest()
