"""Tests for prototyping_funnel.runner — full funnel runner and price-schedule helper.

Covers: run_funnel_stages, FunnelRunConfig, FunnelRunResult, build_funnel_from_price_schedule.
Epic #240, stories #81/#82 (costing + bridge integration).
"""

from __future__ import annotations

import json

import pytest

from prototyping_funnel.cost_model import PriceSchedule, QuantityBreak
from prototyping_funnel.mfg_bridge import DesignSpec
from prototyping_funnel.runner import (
    FunnelRunConfig,
    FunnelRunResult,
    build_funnel_from_price_schedule,
    run_funnel_stages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('runner test STEP'), '2;1');
FILE_NAME('part.step','2026-06-19T00:00:00',(),(),'makerbench','','');
FILE_SCHEMA(('AP214'));
ENDSEC;
DATA;
#1 = PRODUCT('bracket','bracket','', (#2));
#2 = PRODUCT_CONTEXT('',#3,'');
#3 = APPLICATION_CONTEXT('mechanical design');
ENDSEC;
END-ISO-10303-21;
"""


def _step_file(tmp_path):
    p = tmp_path / "part.step"
    p.write_text(MINIMAL_STEP, encoding="utf-8")
    return p


def _cnc_design() -> DesignSpec:
    return DesignSpec(
        name="runner-test-bracket",
        dfm_score=0.0,   # populated by score_file in runner
        material_volume_mm3=12_000,
        removed_volume_mm3=3_000,
        hole_count=6,
        setup_count=1,
        tool_change_count=2,
    )


# ---------------------------------------------------------------------------
# FunnelRunConfig
# ---------------------------------------------------------------------------


def test_run_config_defaults():
    cfg = FunnelRunConfig()
    assert cfg.dfm_pass_threshold == 80.0
    assert cfg.quantity == 1
    assert cfg.unit_sale_price_usd is None


def test_run_config_rejects_bad_threshold():
    with pytest.raises(ValueError, match="dfm_pass_threshold"):
        FunnelRunConfig(dfm_pass_threshold=150.0)


def test_run_config_rejects_zero_quantity():
    with pytest.raises(ValueError, match="quantity"):
        FunnelRunConfig(quantity=0)


# ---------------------------------------------------------------------------
# run_funnel_stages — valid STEP
# ---------------------------------------------------------------------------


def test_runner_full_pipeline_valid_step(tmp_path):
    path = _step_file(tmp_path)
    result = run_funnel_stages(path, _cnc_design())
    assert isinstance(result, FunnelRunResult)
    assert result.dfm_passed is True
    assert "dfm" in result.gates_passed
    assert "quote" in result.gates_passed
    assert result.bridge is not None
    assert result.blocked_at is None


def test_runner_result_has_sha256(tmp_path):
    path = _step_file(tmp_path)
    result = run_funnel_stages(path, _cnc_design())
    assert len(result.geometry_sha256) == 64   # SHA-256 hex


def test_runner_result_is_deterministic(tmp_path):
    path = _step_file(tmp_path)
    design = _cnc_design()
    r1 = run_funnel_stages(path, design)
    r2 = run_funnel_stages(path, design)
    assert r1.to_dict() == r2.to_dict()


def test_runner_result_is_json_serializable(tmp_path):
    path = _step_file(tmp_path)
    result = run_funnel_stages(path, _cnc_design())
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["dfm_passed"] is True


def test_runner_concept_stage_cost_propagates(tmp_path):
    path = _step_file(tmp_path)
    cfg = FunnelRunConfig(concept_cost_usd=250.0, concept_lead_time_days=3.0)
    result = run_funnel_stages(path, _cnc_design(), cfg)
    concept_stages = [s for s in result.stages if s.stage == "concept"]
    assert len(concept_stages) == 1
    assert concept_stages[0].cost_usd == 250.0


def test_runner_bridge_cheapest_is_positive(tmp_path):
    path = _step_file(tmp_path)
    result = run_funnel_stages(path, _cnc_design())
    cheapest = result.bridge.cheapest()
    assert cheapest is not None
    assert cheapest.total_usd > 0


def test_runner_stages_ordered_correctly(tmp_path):
    path = _step_file(tmp_path)
    result = run_funnel_stages(path, _cnc_design())
    stage_names = [s.stage for s in result.stages]
    # concept must come before dfm, dfm before quote, etc.
    from prototyping_funnel.stages import STAGE_ORDER, _STAGE_INDEX
    for i in range(1, len(stage_names)):
        assert _STAGE_INDEX[stage_names[i]] > _STAGE_INDEX[stage_names[i - 1]]


# ---------------------------------------------------------------------------
# run_funnel_stages — DFM failure
# ---------------------------------------------------------------------------


def test_runner_blocks_at_dfm_for_invalid_geometry(tmp_path):
    bad_file = tmp_path / "garbage.step"
    bad_file.write_text("this is not a real STEP file\n", encoding="utf-8")
    result = run_funnel_stages(bad_file, _cnc_design())
    assert result.blocked_at == "dfm"
    assert result.dfm_passed is False
    assert result.bridge is None
    assert "dfm" not in result.gates_passed


def test_runner_blocks_at_dfm_for_nonexistent_file(tmp_path):
    missing = tmp_path / "missing.step"
    result = run_funnel_stages(missing, _cnc_design())
    assert result.blocked_at == "dfm"


# ---------------------------------------------------------------------------
# run_funnel_stages — ROI
# ---------------------------------------------------------------------------


def test_runner_roi_present_when_sale_price_supplied(tmp_path):
    path = _step_file(tmp_path)
    cfg = FunnelRunConfig(unit_sale_price_usd=199.0, quantity=100)
    result = run_funnel_stages(path, _cnc_design(), cfg)
    assert result.roi is not None
    assert result.roi.viable is True


def test_runner_roi_absent_without_sale_price(tmp_path):
    path = _step_file(tmp_path)
    result = run_funnel_stages(path, _cnc_design())
    assert result.roi is None


def test_runner_roi_breakeven_units_reasonable(tmp_path):
    path = _step_file(tmp_path)
    cfg = FunnelRunConfig(
        unit_sale_price_usd=150.0,
        quantity=50,
        concept_cost_usd=500.0,
    )
    result = run_funnel_stages(path, _cnc_design(), cfg)
    assert result.roi is not None
    # Breakeven should be at least 1
    assert result.roi.breakeven_units_ceil >= 1


# ---------------------------------------------------------------------------
# build_funnel_from_price_schedule
# ---------------------------------------------------------------------------


def _fdm_schedule() -> PriceSchedule:
    return PriceSchedule(
        process_id="additive_3d_print",
        material_id="pla-filament",
        breaks=(
            QuantityBreak(moq=1,   unit_price_usd=18.00),
            QuantityBreak(moq=10,  unit_price_usd=12.50),
            QuantityBreak(moq=50,  unit_price_usd=9.00),
            QuantityBreak(moq=200, unit_price_usd=6.75),
        ),
    )


def test_build_funnel_from_schedule_returns_funnel_and_curve():
    funnel, curve = build_funnel_from_price_schedule(
        _fdm_schedule(),
        iteration_quantities=[1, 5, 25, 100],
        name="test-fdm",
    )
    assert len(funnel.iterations) == 4
    assert len(curve.points) == 4


def test_build_funnel_from_schedule_deflationary():
    funnel, curve = build_funnel_from_price_schedule(
        _fdm_schedule(),
        iteration_quantities=[1, 10, 50, 200],
    )
    # Unit prices should be monotonically non-increasing
    unit_prices = curve.unit_cost_series()
    for i in range(1, len(unit_prices)):
        assert unit_prices[i] <= unit_prices[i - 1]


def test_build_funnel_from_schedule_correct_unit_prices():
    sched = _fdm_schedule()
    _, curve = build_funnel_from_price_schedule(sched, iteration_quantities=[1, 10, 50, 200])
    assert curve.points[0].unit_price_usd == 18.00
    assert curve.points[1].unit_price_usd == 12.50
    assert curve.points[2].unit_price_usd == 9.00
    assert curve.points[3].unit_price_usd == 6.75


def test_build_funnel_from_schedule_cumulative_spend():
    sched = _fdm_schedule()
    _, curve = build_funnel_from_price_schedule(sched, iteration_quantities=[1, 10, 50, 200])
    expected = 18*1 + 12.5*10 + 9*50 + 6.75*200
    assert curve.cumulative_spend_usd() == pytest.approx(expected, rel=1e-6)


def test_build_funnel_from_schedule_reduction_fraction_positive():
    _, curve = build_funnel_from_price_schedule(
        _fdm_schedule(),
        iteration_quantities=[1, 10, 50, 200],
    )
    rf = curve.reduction_fraction()
    # (18 - 6.75) / 18 ≈ 0.625
    assert rf is not None
    assert rf == pytest.approx((18 - 6.75) / 18, rel=1e-5)


def test_build_funnel_from_schedule_summary_json_serializable():
    funnel, _ = build_funnel_from_price_schedule(
        _fdm_schedule(),
        iteration_quantities=[1, 10, 50],
    )
    json.dumps(funnel.summary())
