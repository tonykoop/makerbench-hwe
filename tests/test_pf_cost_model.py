"""Tests for prototyping_funnel.cost_model — price-breaks and deflationary curve.

Covers: QuantityBreak, PriceSchedule, IterationCostPoint, DeflationaryCurve.
Epic #240, story #81 (CostingAdapter / unit-economics layer).
"""

from __future__ import annotations

import json
import math

import pytest

from prototyping_funnel.cost_model import (
    DeflationaryCurve,
    IterationCostPoint,
    PriceSchedule,
    QuantityBreak,
    unit_price_at_qty,
)


# ---------------------------------------------------------------------------
# QuantityBreak
# ---------------------------------------------------------------------------


def test_quantity_break_stores_fields():
    b = QuantityBreak(moq=10, unit_price_usd=12.50)
    assert b.moq == 10
    assert b.unit_price_usd == 12.50


def test_quantity_break_rejects_zero_moq():
    with pytest.raises(ValueError, match="moq must be >= 1"):
        QuantityBreak(moq=0, unit_price_usd=5.0)


def test_quantity_break_rejects_negative_price():
    with pytest.raises(ValueError, match="non-negative"):
        QuantityBreak(moq=1, unit_price_usd=-1.0)


# ---------------------------------------------------------------------------
# PriceSchedule
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


def test_price_schedule_resolves_tier_one():
    sched = _fdm_schedule()
    assert sched.unit_price_at(1) == 18.00


def test_price_schedule_resolves_tier_at_boundary():
    sched = _fdm_schedule()
    assert sched.unit_price_at(10) == 12.50


def test_price_schedule_resolves_tier_above_boundary():
    sched = _fdm_schedule()
    assert sched.unit_price_at(49) == 12.50
    assert sched.unit_price_at(50) == 9.00


def test_price_schedule_resolves_highest_tier():
    sched = _fdm_schedule()
    assert sched.unit_price_at(500) == 6.75


def test_price_schedule_total_price():
    sched = _fdm_schedule()
    assert sched.total_price(50) == pytest.approx(50 * 9.00)


def test_price_schedule_rejects_qty_less_than_one():
    sched = _fdm_schedule()
    with pytest.raises(ValueError, match="quantity must be >= 1"):
        sched.unit_price_at(0)


def test_price_schedule_rejects_unsorted_breaks():
    with pytest.raises(ValueError, match="sorted by ascending"):
        PriceSchedule(
            process_id="cnc_milling",
            material_id="6061-al",
            breaks=(
                QuantityBreak(moq=1, unit_price_usd=50),
                QuantityBreak(moq=100, unit_price_usd=30),
                QuantityBreak(moq=10, unit_price_usd=40),  # out of order
            ),
        )


def test_price_schedule_rejects_first_break_moq_not_one():
    with pytest.raises(ValueError, match="moq=1"):
        PriceSchedule(
            process_id="cnc_milling",
            material_id="6061-al",
            breaks=(QuantityBreak(moq=5, unit_price_usd=40),),
        )


def test_price_schedule_rejects_empty_breaks():
    with pytest.raises(ValueError, match="at least one"):
        PriceSchedule(process_id="cnc_milling", material_id="al", breaks=())


def test_price_schedule_rejects_duplicate_moqs():
    with pytest.raises(ValueError, match="duplicate"):
        PriceSchedule(
            process_id="cnc_milling",
            material_id="al",
            breaks=(
                QuantityBreak(moq=1, unit_price_usd=50),
                QuantityBreak(moq=1, unit_price_usd=40),
            ),
        )


def test_unit_price_at_qty_convenience_alias():
    sched = _fdm_schedule()
    assert unit_price_at_qty(sched, 10) == 12.50


def test_price_schedule_as_dict_is_json_serializable():
    sched = _fdm_schedule()
    d = sched.as_dict()
    json.dumps(d)
    assert d["process_id"] == "additive_3d_print"
    assert len(d["breaks"]) == 4
    assert d["breaks"][0]["moq"] == 1


# ---------------------------------------------------------------------------
# IterationCostPoint
# ---------------------------------------------------------------------------


def test_iteration_cost_point_stores_fields():
    pt = IterationCostPoint(
        iteration=1,
        quantity=3,
        unit_price_usd=80.0,
        total_cost_usd=240.0,
        process_id="cnc_milling",
        notes="first prototype run",
    )
    assert pt.unit_price_usd == 80.0
    assert pt.total_cost_usd == 240.0


def test_iteration_cost_point_rejects_zero_iteration():
    with pytest.raises(ValueError, match="iteration must be >= 1"):
        IterationCostPoint(iteration=0, quantity=1, unit_price_usd=10, total_cost_usd=10, process_id="cnc_milling")


def test_iteration_cost_point_rejects_zero_quantity():
    with pytest.raises(ValueError, match="quantity must be >= 1"):
        IterationCostPoint(iteration=1, quantity=0, unit_price_usd=10, total_cost_usd=10, process_id="cnc_milling")


def test_iteration_cost_point_rejects_negative_unit_price():
    with pytest.raises(ValueError, match="unit_price_usd must be non-negative"):
        IterationCostPoint(iteration=1, quantity=1, unit_price_usd=-5, total_cost_usd=10, process_id="cnc_milling")


# ---------------------------------------------------------------------------
# DeflationaryCurve
# ---------------------------------------------------------------------------


def _three_point_curve() -> DeflationaryCurve:
    curve = DeflationaryCurve()
    curve.record(IterationCostPoint(1, quantity=1,  unit_price_usd=220, total_cost_usd=220, process_id="cnc_milling"))
    curve.record(IterationCostPoint(2, quantity=5,  unit_price_usd=80,  total_cost_usd=400, process_id="cnc_milling"))
    curve.record(IterationCostPoint(3, quantity=25, unit_price_usd=38,  total_cost_usd=950, process_id="cnc_milling"))
    return curve


def test_deflationary_curve_records_points():
    curve = _three_point_curve()
    assert len(curve.points) == 3


def test_deflationary_curve_unit_cost_series_is_descending():
    curve = _three_point_curve()
    series = curve.unit_cost_series()
    assert series == [220, 80, 38]
    # Deflationary: each run is cheaper per unit
    assert series == sorted(series, reverse=True)


def test_deflationary_curve_cumulative_spend():
    curve = _three_point_curve()
    assert curve.cumulative_spend_usd() == pytest.approx(220 + 400 + 950)


def test_deflationary_curve_reduction_fraction():
    curve = _three_point_curve()
    # (220 - 38) / 220
    expected = (220 - 38) / 220
    assert curve.reduction_fraction() == pytest.approx(expected, rel=1e-5)


def test_deflationary_curve_reduction_fraction_none_for_single_point():
    curve = DeflationaryCurve()
    curve.record(IterationCostPoint(1, quantity=1, unit_price_usd=100, total_cost_usd=100, process_id="cnc_milling"))
    assert curve.reduction_fraction() is None


def test_deflationary_curve_learning_rate_positive():
    curve = _three_point_curve()
    lr = curve.learning_rate()
    assert lr is not None
    # Cost is dropping fast: learning rate should be > 0 (improvement)
    assert lr > 0


def test_deflationary_curve_learning_rate_none_for_single_point():
    curve = DeflationaryCurve()
    curve.record(IterationCostPoint(1, quantity=1, unit_price_usd=100, total_cost_usd=100, process_id="cnc_milling"))
    assert curve.learning_rate() is None


def test_deflationary_curve_learning_rate_zero_for_flat_cost():
    curve = DeflationaryCurve()
    curve.record(IterationCostPoint(1, quantity=1, unit_price_usd=100, total_cost_usd=100, process_id="cnc_milling"))
    curve.record(IterationCostPoint(2, quantity=1, unit_price_usd=100, total_cost_usd=100, process_id="cnc_milling"))
    lr = curve.learning_rate()
    # Flat cost: ratio=1, log(1)=0 → lr = 1 - 2^0 = 0
    assert lr == pytest.approx(0.0, abs=1e-9)


def test_deflationary_curve_projected_unit_cost():
    curve = _three_point_curve()
    proj = curve.projected_unit_cost(at_iteration=6)
    # Must be lower than current last point (38) — deeper into the curve
    assert proj is not None
    # Sanity: projection for iteration 3 should match roughly (not exact due to log fit)
    proj_3 = curve.projected_unit_cost(at_iteration=3)
    assert proj_3 == pytest.approx(38.0, rel=0.01)


def test_deflationary_curve_rejects_non_sequential_iterations():
    curve = DeflationaryCurve()
    curve.record(IterationCostPoint(1, quantity=1, unit_price_usd=100, total_cost_usd=100, process_id="cnc_milling"))
    with pytest.raises(ValueError, match="sequential"):
        curve.record(IterationCostPoint(3, quantity=1, unit_price_usd=80, total_cost_usd=80, process_id="cnc_milling"))


def test_deflationary_curve_as_dict_is_json_serializable():
    curve = _three_point_curve()
    d = curve.as_dict()
    json.dumps(d)
    assert d["point_count"] == 3
    assert d["cumulative_spend_usd"] == pytest.approx(1570.0)
    assert "reduction_fraction" in d
    assert "learning_rate" in d
    series = d["series"]
    assert series[0]["iteration"] == 1
    assert series[0]["unit_price_usd"] == 220
