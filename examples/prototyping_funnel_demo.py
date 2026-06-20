#!/usr/bin/env python3
"""Deflationary prototyping funnel demo (epic #240).

Shows the full pipeline from a geometry file through the stage model,
multi-process manufacturing bridge, and ROI/breakeven analysis.

Run it::

    python examples/prototyping_funnel_demo.py

Or with the installed CLI::

    makerbench-protofunnel examples/closed_loop_instrument_bridge.step \\
        --name "instrument-bridge" \\
        --material-volume-mm3 8000 \\
        --hole-count 2 \\
        --sale-price 120 \\
        --quantity 100 \\
        --json
"""

from __future__ import annotations

import json
from pathlib import Path

from prototyping_funnel.cost_model import (
    DeflationaryCurve,
    IterationCostPoint,
    PriceSchedule,
    QuantityBreak,
)
from prototyping_funnel.mfg_bridge import DesignSpec, ManufacturingBridge
from prototyping_funnel.report import build_report_with_roi
from prototyping_funnel.roi import ROIInput
from prototyping_funnel.runner import build_funnel_from_price_schedule, run_funnel_stages
from prototyping_funnel.stages import PrototypingFunnel, IterationRecord, StageResult

ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# 1. Run the full stage pipeline on the existing instrument-bridge STEP
# ---------------------------------------------------------------------------
print("=" * 60)
print("PART 1 — Full funnel stage pipeline")
print("=" * 60)

STEP = ROOT / "examples" / "closed_loop_instrument_bridge.step"

design = DesignSpec(
    name="instrument-bridge",
    dfm_score=0.0,      # populated by score_file inside runner
    material_volume_mm3=8_000,
    hole_count=2,
    setup_count=1,
)

from prototyping_funnel.runner import FunnelRunConfig

config = FunnelRunConfig(
    concept_cost_usd=200.0,
    concept_lead_time_days=1.0,
    unit_sale_price_usd=120.0,
    quantity=100,
)

result = run_funnel_stages(STEP, design, config)
print(json.dumps(result.to_dict(), indent=2))

# ---------------------------------------------------------------------------
# 2. Deflationary curve from a PLA price schedule
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("PART 2 — Deflationary curve from PLA tiered pricing")
print("=" * 60)

pla_schedule = PriceSchedule(
    process_id="additive_3d_print",
    material_id="pla-filament",
    breaks=(
        QuantityBreak(moq=1,   unit_price_usd=22.00),
        QuantityBreak(moq=5,   unit_price_usd=16.50),
        QuantityBreak(moq=25,  unit_price_usd=10.00),
        QuantityBreak(moq=100, unit_price_usd=7.20),
    ),
)

funnel, curve = build_funnel_from_price_schedule(
    pla_schedule,
    iteration_quantities=[1, 5, 25, 100],
    name="instrument-bridge-pla",
)

print("Deflationary curve:")
for pt in curve.points:
    print(
        f"  iter {pt.iteration}: qty={pt.quantity:3d}  "
        f"unit=${pt.unit_price_usd:.2f}  total=${pt.total_cost_usd:.2f}"
    )
print(f"Unit-cost reduction: {curve.reduction_fraction():.1%}")
print(f"Learning rate:       {curve.learning_rate():.1%} per doubling")

# ---------------------------------------------------------------------------
# 3. Full report with ROI and sensitivity
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("PART 3 — Deflationary report with ROI projection")
print("=" * 60)

roi_input = ROIInput(
    development_cost_usd=funnel.cumulative_cost_usd(),
    unit_manufacturing_cost_usd=pla_schedule.unit_price_at(100),
    unit_sale_price_usd=25.0,
    target_volume=500,
)

report = build_report_with_roi(funnel, roi_input, curve=curve)
print(report.text_summary())

# ---------------------------------------------------------------------------
# 4. Multi-process manufacturing bridge
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("PART 4 — Multi-process cost bridge for a CNC bracket")
print("=" * 60)

cnc_design = DesignSpec(
    name="motor-mount-bracket",
    dfm_score=91.5,
    material_volume_mm3=15_000,
    removed_volume_mm3=4_500,
    hole_count=8,
    setup_count=1,
    tool_change_count=3,
)

bridge = ManufacturingBridge()
bridge_result = bridge.evaluate(cnc_design)
print(f"Process profiles evaluated: {len(bridge_result.results)}")
print(f"Cheapest: {bridge_result.cheapest().profile_id} @ ${bridge_result.cheapest().total_usd:.2f}")
print("\nAll results (ranked cheapest → most expensive):")
for r in bridge_result.results:
    print(f"  {r.rank}. {r.profile_id:35s} ${r.total_usd:8.2f}")
