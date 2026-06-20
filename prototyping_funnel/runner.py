"""Full deflationary-funnel runner: geometry → stages → bridge → ROI.

The runner wires together the three existing layers without duplicating any
oracle/scoring/answer-key logic:

  1. Run the MakerBench core DFM scorer via :func:`makerbench_core.score_file`.
  2. Score the geometry through the stage model (:mod:`.stages`).
  3. Evaluate the design against all manufacturing process profiles
     (:mod:`.mfg_bridge`).
  4. Project ROI and breakeven for the cheapest manufacturing process
     (:mod:`.roi`).

Returns a self-contained :class:`FunnelRunResult` that is JSON-serializable
and deterministic for identical inputs.

Story coverage: #81, #82, #240.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from makerbench_core import score_file

from .cost_model import DeflationaryCurve, IterationCostPoint, PriceSchedule
from .mfg_bridge import BridgeResult, DesignSpec, ManufacturingBridge
from .roi import BreakevenResult, ROIInput, breakeven_analysis
from .stages import (
    IterationRecord,
    PrototypingFunnel,
    Stage,
    StageResult,
)


@dataclass(frozen=True)
class FunnelRunConfig:
    """Configuration for one funnel run.

    ``unit_sale_price_usd`` and ``development_overhead_usd`` are optional ROI
    inputs.  When omitted, the ROI section is skipped.
    """

    dfm_pass_threshold: float = 80.0       # minimum DFM score to proceed
    concept_cost_usd: float = 0.0          # design / CAD NRE cost
    concept_lead_time_days: float = 0.0
    unit_sale_price_usd: float | None = None   # triggers ROI analysis
    development_overhead_usd: float = 0.0  # extra NRE not captured in stages
    quantity: int = 1
    bridge_profile_ids: list[str] | None = None  # None = all presets

    def __post_init__(self) -> None:
        if self.dfm_pass_threshold < 0 or self.dfm_pass_threshold > 100:
            raise ValueError("dfm_pass_threshold must be in [0, 100]")
        if self.quantity < 1:
            raise ValueError("quantity must be >= 1")


@dataclass(frozen=True)
class FunnelRunResult:
    """Output of :func:`run_funnel_stages`."""

    geometry_path: str
    geometry_sha256: str
    dfm_score: float
    dfm_passed: bool
    stages: tuple[StageResult, ...]
    bridge: BridgeResult | None
    roi: BreakevenResult | None
    blocked_at: Stage | None
    notes: list[str]

    @property
    def gates_passed(self) -> list[Stage]:
        return [s.stage for s in self.stages if s.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry_path": self.geometry_path,
            "geometry_sha256": self.geometry_sha256,
            "dfm_score": self.dfm_score,
            "dfm_passed": self.dfm_passed,
            "gates_passed": self.gates_passed,
            "blocked_at": self.blocked_at,
            "stages": [s.as_dict() for s in self.stages],
            "bridge": self.bridge.as_dict() if self.bridge else None,
            "roi": self.roi.as_dict() if self.roi else None,
            "notes": list(self.notes),
        }


def run_funnel_stages(
    geometry_path: str | Path,
    design: DesignSpec,
    config: FunnelRunConfig | None = None,
) -> FunnelRunResult:
    """Run a geometry file through the full deflationary funnel stage model.

    Gates are: concept → dfm → quote (bridge) → prototype → roi.

    The gate model is additive: completing each gate unlocks the next.
    If DFM fails, the run stops at the DFM stage.

    Args:
        geometry_path: Path to any format accepted by :func:`makerbench_core.score_file`.
        design: Lightweight :class:`~.mfg_bridge.DesignSpec` with geometry metrics.
        config: Optional :class:`FunnelRunConfig`; defaults to ``FunnelRunConfig()``.

    Returns:
        A deterministic :class:`FunnelRunResult`.
    """
    cfg = config or FunnelRunConfig()
    path = Path(geometry_path)
    notes: list[str] = []
    stages: list[StageResult] = []

    # SHA256 for reproducibility / citation
    geometry_sha256: str
    try:
        data = path.read_bytes()
        geometry_sha256 = hashlib.sha256(data).hexdigest()
    except OSError as exc:
        geometry_sha256 = "unreadable"
        notes.append(f"could not read geometry: {exc}")

    # --- Stage: concept -------------------------------------------------------
    stages.append(StageResult(
        stage="concept",
        passed=True,
        cost_usd=cfg.concept_cost_usd,
        lead_time_days=cfg.concept_lead_time_days,
        notes=["concept stage: design/CAD NRE"],
    ))

    # --- Stage: dfm -----------------------------------------------------------
    try:
        dfm_result = score_file(path)
        dfm_score = dfm_result.makerbench_dfm_score
        dfm_passed = dfm_result.passed and dfm_score >= cfg.dfm_pass_threshold
    except Exception as exc:  # noqa: BLE001
        dfm_score = 0.0
        dfm_passed = False
        notes.append(f"DFM scorer raised: {exc}")

    dfm_notes: list[str] = [
        f"makerbench_dfm_score={dfm_score:.1f}% (threshold={cfg.dfm_pass_threshold:.1f}%)"
    ]
    stages.append(StageResult(
        stage="dfm",
        passed=dfm_passed,
        cost_usd=0.0,
        lead_time_days=0.0,
        notes=dfm_notes,
    ))

    if not dfm_passed:
        notes.append(
            f"blocked at DFM: score {dfm_score:.1f}% < threshold {cfg.dfm_pass_threshold:.1f}%"
        )
        return FunnelRunResult(
            geometry_path=str(path),
            geometry_sha256=geometry_sha256,
            dfm_score=dfm_score,
            dfm_passed=False,
            stages=tuple(stages),
            bridge=None,
            roi=None,
            blocked_at="dfm",
            notes=notes,
        )

    # --- Stage: quote (manufacturing bridge) ---------------------------------
    bridge = ManufacturingBridge(profile_ids=cfg.bridge_profile_ids)
    bridge_result = bridge.evaluate(design)
    cheapest = bridge_result.cheapest()

    bridge_notes: list[str] = []
    if cheapest:
        bridge_notes.append(
            f"cheapest process: {cheapest.profile_id} @ ${cheapest.total_usd:.2f}"
        )
    else:
        bridge_notes.append("no compatible process profiles for this design geometry")
        notes.append("quote stage: no compatible process profiles")

    stages.append(StageResult(
        stage="quote",
        passed=bool(cheapest),
        cost_usd=0.0,
        lead_time_days=0.0,
        notes=bridge_notes,
    ))

    if not cheapest:
        return FunnelRunResult(
            geometry_path=str(path),
            geometry_sha256=geometry_sha256,
            dfm_score=dfm_score,
            dfm_passed=True,
            stages=tuple(stages),
            bridge=bridge_result,
            roi=None,
            blocked_at="quote",
            notes=notes,
        )

    # --- Stage: prototype ----------------------------------------------------
    prototype_cost = cheapest.total_usd * max(cfg.quantity, 1)
    stages.append(StageResult(
        stage="prototype",
        passed=True,
        cost_usd=prototype_cost,
        lead_time_days=0.0,
        notes=[f"estimated cost at qty={cfg.quantity}: ${prototype_cost:.2f}"],
    ))

    # --- Stage: validation (ROI / breakeven) ---------------------------------
    roi_result: BreakevenResult | None = None
    roi_notes: list[str] = []
    roi_passed = True
    if cfg.unit_sale_price_usd is not None and cfg.unit_sale_price_usd > 0:
        total_dev_cost = (
            cfg.concept_cost_usd
            + prototype_cost
            + cfg.development_overhead_usd
        )
        roi_input = ROIInput(
            development_cost_usd=total_dev_cost,
            unit_manufacturing_cost_usd=cheapest.total_usd,
            unit_sale_price_usd=cfg.unit_sale_price_usd,
            target_volume=max(cfg.quantity, 1),
        )
        roi_result = breakeven_analysis(roi_input)
        roi_notes.append(
            f"breakeven at {roi_result.breakeven_units_ceil} units; "
            f"ROI at target volume {cfg.quantity}: {roi_result.roi_at_target:.2%}"
        )
        roi_passed = roi_result.viable
    else:
        roi_notes.append("ROI skipped: unit_sale_price_usd not provided")

    stages.append(StageResult(
        stage="validation",
        passed=roi_passed,
        cost_usd=0.0,
        lead_time_days=0.0,
        notes=roi_notes,
    ))

    return FunnelRunResult(
        geometry_path=str(path),
        geometry_sha256=geometry_sha256,
        dfm_score=dfm_score,
        dfm_passed=True,
        stages=tuple(stages),
        bridge=bridge_result,
        roi=roi_result,
        blocked_at=None,
        notes=notes,
    )


def build_funnel_from_price_schedule(
    schedule: PriceSchedule,
    iteration_quantities: list[int],
    name: str = "prototype",
) -> tuple[PrototypingFunnel, DeflationaryCurve]:
    """Build a :class:`~.stages.PrototypingFunnel` and :class:`~.cost_model.DeflationaryCurve`
    from a price schedule and a series of iteration quantities.

    Useful for "what does the deflationary curve look like if we run 1, 5, 25, 100 units
    across four iterations?" without needing real geometry.

    Returns a tuple of ``(funnel, curve)`` ready for :meth:`PrototypingFunnel.summary`
    and :meth:`DeflationaryCurve.as_dict`.
    """
    funnel = PrototypingFunnel(name=name)
    curve = DeflationaryCurve()

    for i, qty in enumerate(iteration_quantities, 1):
        unit_price = schedule.unit_price_at(qty)
        total = schedule.total_price(qty)

        record = IterationRecord(
            iteration=i,
            stages=(
                StageResult(
                    stage="prototype",
                    passed=True,
                    cost_usd=total,
                    lead_time_days=0.0,
                    notes=[
                        f"qty={qty} @ ${unit_price:.2f}/unit via {schedule.process_id}:{schedule.material_id}"
                    ],
                ),
            ),
        )
        funnel.add_iteration(record)

        curve.record(IterationCostPoint(
            iteration=i,
            quantity=qty,
            unit_price_usd=unit_price,
            total_cost_usd=total,
            process_id=schedule.process_id,
        ))

    return funnel, curve
