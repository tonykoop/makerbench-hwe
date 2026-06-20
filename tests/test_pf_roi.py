"""Tests for prototyping_funnel.roi — breakeven and ROI analysis.

Covers: ROIInput, BreakevenResult, breakeven_analysis, roi_at_volume,
        SensitivityTable, SensitivityRow.
Epic #240 — deflationary prototyping funnel.
"""

from __future__ import annotations

import json
import math

import pytest

from prototyping_funnel.roi import (
    ROIInput,
    SensitivityTable,
    breakeven_analysis,
    roi_at_volume,
)


# ---------------------------------------------------------------------------
# ROIInput validation
# ---------------------------------------------------------------------------


def test_roi_input_basic():
    roi = ROIInput(
        development_cost_usd=5_000,
        unit_manufacturing_cost_usd=30.0,
        unit_sale_price_usd=99.0,
        target_volume=200,
    )
    assert roi.development_cost_usd == 5_000


def test_roi_input_rejects_negative_dev_cost():
    with pytest.raises(ValueError, match="development_cost_usd"):
        ROIInput(development_cost_usd=-1, unit_manufacturing_cost_usd=10, unit_sale_price_usd=20, target_volume=100)


def test_roi_input_rejects_negative_unit_cost():
    with pytest.raises(ValueError, match="unit_manufacturing_cost_usd"):
        ROIInput(development_cost_usd=100, unit_manufacturing_cost_usd=-5, unit_sale_price_usd=20, target_volume=100)


def test_roi_input_rejects_zero_sale_price():
    with pytest.raises(ValueError, match="unit_sale_price_usd"):
        ROIInput(development_cost_usd=100, unit_manufacturing_cost_usd=10, unit_sale_price_usd=0, target_volume=100)


def test_roi_input_rejects_zero_target_volume():
    with pytest.raises(ValueError, match="target_volume"):
        ROIInput(development_cost_usd=100, unit_manufacturing_cost_usd=10, unit_sale_price_usd=20, target_volume=0)


# ---------------------------------------------------------------------------
# breakeven_analysis — happy path
# ---------------------------------------------------------------------------


def _standard_roi() -> ROIInput:
    return ROIInput(
        development_cost_usd=5_000,
        unit_manufacturing_cost_usd=30.0,
        unit_sale_price_usd=99.0,
        target_volume=200,
    )


def test_breakeven_is_viable():
    result = breakeven_analysis(_standard_roi())
    assert result.viable is True


def test_breakeven_units_formula():
    roi = _standard_roi()
    # contribution margin = 99 - 30 = 69; breakeven = 5000 / 69
    result = breakeven_analysis(roi)
    expected = 5_000 / 69.0
    assert result.breakeven_units == pytest.approx(expected, rel=1e-6)


def test_breakeven_units_ceil_is_integer_ceiling():
    result = breakeven_analysis(_standard_roi())
    assert result.breakeven_units_ceil == math.ceil(result.breakeven_units)
    assert isinstance(result.breakeven_units_ceil, int)


def test_breakeven_revenue_matches_ceil():
    result = breakeven_analysis(_standard_roi())
    expected_rev = result.breakeven_units_ceil * 99.0
    assert result.breakeven_revenue_usd == pytest.approx(expected_rev, rel=1e-6)


def test_roi_at_target_is_positive_beyond_breakeven():
    result = breakeven_analysis(_standard_roi())
    assert result.roi_at_target > 0


def test_roi_at_target_formula():
    roi = _standard_roi()
    result = breakeven_analysis(roi)
    revenue = 200 * 99.0
    variable_cost = 200 * 30.0
    net = revenue - variable_cost - 5_000
    expected_roi = net / 5_000
    assert result.roi_at_target == pytest.approx(expected_roi, rel=1e-6)


def test_net_profit_at_target():
    roi = _standard_roi()
    result = breakeven_analysis(roi)
    expected = 200 * 99 - 200 * 30 - 5_000
    assert result.net_profit_at_target_usd == pytest.approx(expected, rel=1e-6)


def test_gross_margin_fraction():
    result = breakeven_analysis(_standard_roi())
    # (99 - 30) / 99
    assert result.gross_margin_fraction == pytest.approx(69 / 99, rel=1e-6)


def test_breakeven_result_as_dict_is_json_serializable():
    result = breakeven_analysis(_standard_roi())
    d = result.as_dict()
    json.dumps(d)
    assert d["viable"] is True
    assert "breakeven_units" in d
    assert "roi_at_target" in d


# ---------------------------------------------------------------------------
# breakeven_analysis — edge cases
# ---------------------------------------------------------------------------


def test_breakeven_not_viable_when_price_equals_cost():
    roi = ROIInput(
        development_cost_usd=1_000,
        unit_manufacturing_cost_usd=50.0,
        unit_sale_price_usd=50.0,
        target_volume=100,
    )
    result = breakeven_analysis(roi)
    assert result.viable is False
    assert result.breakeven_units == math.inf


def test_breakeven_not_viable_when_price_below_cost():
    roi = ROIInput(
        development_cost_usd=500,
        unit_manufacturing_cost_usd=80.0,
        unit_sale_price_usd=60.0,
        target_volume=50,
    )
    result = breakeven_analysis(roi)
    assert result.viable is False
    assert result.breakeven_units == math.inf
    assert result.roi_at_target < 0


def test_breakeven_zero_dev_cost_is_already_profitable():
    roi = ROIInput(
        development_cost_usd=0.0,
        unit_manufacturing_cost_usd=20.0,
        unit_sale_price_usd=50.0,
        target_volume=100,
    )
    result = breakeven_analysis(roi)
    assert result.viable is True
    assert result.breakeven_units == 0.0
    assert result.breakeven_units_ceil == 0


def test_breakeven_at_target_volume_equal_to_breakeven():
    # exactly at breakeven volume, ROI ~ 0
    margin = 69.0
    dev_cost = 5_000.0
    be_units = dev_cost / margin  # ~72.46...
    be_ceil = math.ceil(be_units)
    roi = ROIInput(
        development_cost_usd=dev_cost,
        unit_manufacturing_cost_usd=30.0,
        unit_sale_price_usd=99.0,
        target_volume=be_ceil,
    )
    result = breakeven_analysis(roi)
    # At or just above breakeven, net profit >= 0
    assert result.net_profit_at_target_usd >= 0


# ---------------------------------------------------------------------------
# roi_at_volume convenience function
# ---------------------------------------------------------------------------


def test_roi_at_volume_matches_breakeven_analysis():
    roi_direct = breakeven_analysis(_standard_roi()).roi_at_target
    roi_fn = roi_at_volume(5_000, 30.0, 99.0, 200)
    assert roi_fn == pytest.approx(roi_direct, rel=1e-9)


def test_roi_at_volume_increases_with_volume():
    r1 = roi_at_volume(5_000, 30.0, 99.0, 100)
    r2 = roi_at_volume(5_000, 30.0, 99.0, 500)
    assert r2 > r1


# ---------------------------------------------------------------------------
# SensitivityTable
# ---------------------------------------------------------------------------


def test_sensitivity_table_unit_cost_sweep_length():
    table = SensitivityTable(_standard_roi())
    deltas = [-0.20, -0.10, 0.0, 0.10]
    rows = table.sweep_unit_cost(deltas)
    assert len(rows) == len(deltas)


def test_sensitivity_table_unit_cost_lower_cost_gives_fewer_breakeven_units():
    table = SensitivityTable(_standard_roi())
    rows = table.sweep_unit_cost([-0.20, 0.0, 0.20])
    # Lower unit cost → lower breakeven
    be_low_cost = rows[0].breakeven_units
    be_base = rows[1].breakeven_units
    be_high_cost = rows[2].breakeven_units
    assert be_low_cost < be_base < be_high_cost


def test_sensitivity_table_sale_price_sweep_labels():
    table = SensitivityTable(_standard_roi())
    rows = table.sweep_sale_price([-0.10, 0.10])
    labels = [r.scenario_label for r in rows]
    assert "sale_price-10%" in labels
    assert "sale_price+10%" in labels


def test_sensitivity_table_base_row_is_viable():
    table = SensitivityTable(_standard_roi())
    rows = table.sweep_unit_cost([0.0])
    assert rows[0].viable is True


def test_sensitivity_table_high_unit_cost_may_become_unviable():
    # If we bump unit cost to 200% delta → new cost = 30 * 3 = 90 which is still < 99 (viable)
    # So let's use +300% → 120 > 99 → not viable
    table = SensitivityTable(_standard_roi())
    rows = table.sweep_unit_cost([3.0])   # 400% of original → 120 > 99
    assert rows[0].viable is False


def test_sensitivity_table_as_dict_is_json_serializable():
    table = SensitivityTable(_standard_roi())
    d = table.as_dict(deltas=[-0.20, 0.0, 0.20])
    json.dumps(d)
    assert "unit_cost_sweep" in d
    assert "sale_price_sweep" in d
    assert len(d["unit_cost_sweep"]) == 3
    assert len(d["sale_price_sweep"]) == 3


def test_sensitivity_row_as_dict_keys():
    table = SensitivityTable(_standard_roi())
    rows = table.sweep_unit_cost([0.0])
    d = rows[0].as_dict()
    for key in ("scenario_label", "unit_manufacturing_cost_usd", "unit_sale_price_usd",
                 "breakeven_units", "breakeven_units_ceil", "roi_at_target", "viable"):
        assert key in d, f"missing key: {key}"
