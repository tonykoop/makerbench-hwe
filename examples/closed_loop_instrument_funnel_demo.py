#!/usr/bin/env python3
"""Closed-loop instrument demo, Phase 3b: DFM + manufacturing-cost gate (#83).

The original closed-loop demo (``docs/CLOSED_LOOP_INSTRUMENT_DEMO.md``) routes a
code-CAD-generated lyre/kora bridge through MakerBench's deterministic acoustic
``scale_length_check`` gate. This script extends that loop by *also* routing the
exported STEP through the deflationary prototyping funnel built for epic #240:

    Gate 1  deterministic DFM        -> makerbench_core.score_file (via run_funnel)
    Gate 2  local cost estimate      -> makerbench.costing (FDM/PLA prototype)
    Gate 3  vendor quote (stub)      -> makerbench.quote_bridge
    Gate 4  human approval request   -> terminal; never places an order

So the pilot instrument passes BOTH gates: it is acoustically correct (scale
length) AND manufacturable + costed. Everything is deterministic and offline —
the default quote adapter is the stub, and the STEP carries only public-safe
generic geometry.

Run it::

    python examples/closed_loop_instrument_funnel_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from makerbench.costing import GeometryCostMetrics, get_profile
from makerbench.funnel import run_funnel
from makerbench.instrument_acoustics_ladder import scale_length_check

# Repo-relative geometry path recorded in the captured result (portable).
GEOMETRY_REL = "examples/closed_loop_instrument_bridge.step"

# Public seed-0 acoustic parameters, shared with the acoustic-gate demo.
ACOUSTIC_PARAMS = {
    "declared_scale_mm": 650.0,
    "target_scale_mm": 650.0,
    "scale_tolerance_mm": 2.0,
    "nut_to_bridge_mm": 653.0,
    "saddle_intonation_mm": 3.0,
}

# A 3D-printed prototype bridge: small PLA part. Metrics are declared public
# fixtures (a slicer would supply print_time_minutes in a real run).
COST_METRICS = GeometryCostMetrics(
    material_volume_mm3=9_000.0,
    support_material_volume_mm3=600.0,
    print_time_minutes=95.0,
    setup_count=1,
)


def build_combined_result() -> dict:
    """Run both gates and return a portable, deterministic result summary."""
    step_path = Path(__file__).resolve().parent / "closed_loop_instrument_bridge.step"

    funnel = run_funnel(
        step_path,
        quote_process="3d_printing",
        material="pla",
        cost_metrics=COST_METRICS,
        cost_profile=get_profile("fdm-pla-v1"),
        requested_by="closed-loop-instrument-demo",
    )

    acoustics = scale_length_check(ACOUSTIC_PARAMS)

    manufacturing_passed = funnel.blocked_at is None
    acoustic_passed = acoustics["feasible"] == 1.0

    return {
        "pilot": "lyre-kora-bridge",
        "geometry": GEOMETRY_REL,
        "dfm": {
            "score": funnel.dfm_score,
            "passed": funnel.dfm_passed,
            "profile": funnel.dfm["profile"],
        },
        "manufacturing": {
            "gates_passed": funnel.gates_passed,
            "blocked_at": funnel.blocked_at,
            "process": "3d_printing",
            "cost_profile": "fdm-pla-v1",
            "local_cost_usd": funnel.cost_estimate.total_usd if funnel.cost_estimate else None,
            "quote_vendor": funnel.quote.vendor if funnel.quote else None,
            "quote_status": funnel.quote.status if funnel.quote else None,
            "quote_price_usd": funnel.quote.price_usd if funnel.quote else None,
            "purchase_order_allowed": funnel.purchase_order_allowed,
        },
        "acoustics": acoustics,
        "both_gates_passed": bool(manufacturing_passed and acoustic_passed),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(build_combined_result(), indent=2, sort_keys=True))
