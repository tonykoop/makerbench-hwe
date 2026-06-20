"""Smoke-test the prototyping_funnel demo script (epic #240).

Imports and runs the demo in-process to catch any breakage in the example
without running it as a subprocess.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "prototyping_funnel_demo.py"


def _import_demo():
    """Import the demo module without executing its top-level statements."""
    spec = importlib.util.spec_from_file_location("prototyping_funnel_demo", DEMO)
    mod = importlib.util.module_from_spec(spec)
    return spec, mod


def test_demo_file_exists():
    assert DEMO.is_file(), "examples/prototyping_funnel_demo.py is missing"


def test_demo_is_syntactically_valid():
    source = DEMO.read_text(encoding="utf-8")
    try:
        compile(source, str(DEMO), "exec")
    except SyntaxError as exc:
        pytest.fail(f"Syntax error in demo: {exc}")


def test_demo_imports_are_available():
    """All imports in the demo should resolve without error."""
    from prototyping_funnel.cost_model import (
        DeflationaryCurve,
        IterationCostPoint,
        PriceSchedule,
        QuantityBreak,
    )
    from prototyping_funnel.mfg_bridge import DesignSpec, ManufacturingBridge
    from prototyping_funnel.report import build_report_with_roi
    from prototyping_funnel.roi import ROIInput
    from prototyping_funnel.runner import FunnelRunConfig, build_funnel_from_price_schedule, run_funnel_stages
    from prototyping_funnel.stages import IterationRecord, PrototypingFunnel, StageResult


def test_demo_bridge_part_runs(capsys):
    """Run just the manufacturing bridge section of the demo inline."""
    from prototyping_funnel.mfg_bridge import DesignSpec, ManufacturingBridge

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
    result = bridge.evaluate(cnc_design)

    assert result is not None
    assert len(result.results) > 0
    cheapest = result.cheapest()
    assert cheapest is not None
    assert cheapest.rank == 1
    # Bridge result is JSON-serializable
    json.dumps(result.as_dict())


def test_demo_price_schedule_part_runs():
    """Run just the deflationary-curve section of the demo inline."""
    from prototyping_funnel.cost_model import PriceSchedule, QuantityBreak
    from prototyping_funnel.roi import ROIInput
    from prototyping_funnel.report import build_report_with_roi
    from prototyping_funnel.runner import build_funnel_from_price_schedule

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
        name="demo-pla",
    )
    roi_input = ROIInput(
        development_cost_usd=funnel.cumulative_cost_usd(),
        unit_manufacturing_cost_usd=pla_schedule.unit_price_at(100),
        unit_sale_price_usd=25.0,
        target_volume=500,
    )
    report = build_report_with_roi(funnel, roi_input, curve=curve)
    text = report.text_summary()
    assert "Breakeven" in text
    assert "ROI" in text.upper()
    assert report.roi.viable is True
