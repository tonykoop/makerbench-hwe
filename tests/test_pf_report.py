"""Tests for prototyping_funnel.report — DeflationaryReport, build_report, build_report_with_roi.

Epic #240 — deflationary prototyping funnel.
"""

from __future__ import annotations

import json

import pytest

from prototyping_funnel.cost_model import DeflationaryCurve, IterationCostPoint, PriceSchedule, QuantityBreak
from prototyping_funnel.report import DeflationaryReport, build_report, build_report_with_roi
from prototyping_funnel.roi import ROIInput
from prototyping_funnel.runner import build_funnel_from_price_schedule
from prototyping_funnel.stages import IterationRecord, PrototypingFunnel, StageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_funnel(n_iters: int = 3) -> PrototypingFunnel:
    funnel = PrototypingFunnel("report-test")
    costs = [600.0, 350.0, 200.0][:n_iters]
    days = [10.0, 8.0, 6.0][:n_iters]
    for i, (cost, d) in enumerate(zip(costs, days), 1):
        funnel.add_iteration(IterationRecord(
            iteration=i,
            stages=(
                StageResult("concept", passed=True, cost_usd=cost * 0.3, lead_time_days=d * 0.3),
                StageResult("prototype", passed=True, cost_usd=cost * 0.7, lead_time_days=d * 0.7),
            ),
        ))
    return funnel


def _make_curve() -> DeflationaryCurve:
    curve = DeflationaryCurve()
    curve.record(IterationCostPoint(1, quantity=1,  unit_price_usd=220, total_cost_usd=220, process_id="cnc_milling"))
    curve.record(IterationCostPoint(2, quantity=5,  unit_price_usd=80,  total_cost_usd=400, process_id="cnc_milling"))
    curve.record(IterationCostPoint(3, quantity=25, unit_price_usd=38,  total_cost_usd=950, process_id="cnc_milling"))
    return curve


def _standard_roi() -> ROIInput:
    return ROIInput(
        development_cost_usd=5_000.0,
        unit_manufacturing_cost_usd=30.0,
        unit_sale_price_usd=99.0,
        target_volume=200,
    )


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


def test_build_report_basic():
    funnel = _make_funnel()
    report = build_report(funnel)
    assert isinstance(report, DeflationaryReport)
    assert report.name == "report-test"
    assert report.iteration_count == 3


def test_build_report_cumulative_cost_matches_funnel():
    funnel = _make_funnel()
    report = build_report(funnel)
    assert report.cumulative_cost_usd == pytest.approx(funnel.cumulative_cost_usd())


def test_build_report_cost_reduction_fraction():
    funnel = _make_funnel()
    report = build_report(funnel)
    assert report.cost_reduction_fraction is not None
    assert report.cost_reduction_fraction > 0


def test_build_report_with_curve_includes_stats():
    funnel = _make_funnel()
    curve = _make_curve()
    report = build_report(funnel, curve=curve)
    assert report.curve_stats is not None
    assert "learning_rate" in report.curve_stats


def test_build_report_without_curve_has_no_stats():
    funnel = _make_funnel()
    report = build_report(funnel)
    assert report.curve_stats is None


def test_build_report_roi_is_none():
    funnel = _make_funnel()
    report = build_report(funnel)
    assert report.roi is None
    assert report.sensitivity is None


def test_build_report_to_dict_json_serializable():
    funnel = _make_funnel()
    report = build_report(funnel)
    json.dumps(report.to_dict())


def test_build_report_to_json_is_valid_json():
    funnel = _make_funnel()
    report = build_report(funnel)
    payload = json.loads(report.to_json())
    assert "cumulative_cost_usd" in payload


# ---------------------------------------------------------------------------
# build_report_with_roi
# ---------------------------------------------------------------------------


def test_build_report_with_roi_includes_roi():
    funnel = _make_funnel()
    report = build_report_with_roi(funnel, _standard_roi())
    assert report.roi is not None
    assert report.roi.viable is True


def test_build_report_with_roi_includes_sensitivity():
    funnel = _make_funnel()
    report = build_report_with_roi(funnel, _standard_roi())
    assert report.sensitivity is not None
    assert "unit_cost_sweep" in report.sensitivity


def test_build_report_with_roi_custom_deltas():
    funnel = _make_funnel()
    deltas = [-0.10, 0.0, 0.10]
    report = build_report_with_roi(funnel, _standard_roi(), sensitivity_deltas=deltas)
    assert len(report.sensitivity["unit_cost_sweep"]) == 3
    assert len(report.sensitivity["sale_price_sweep"]) == 3


def test_build_report_with_roi_is_json_serializable():
    funnel = _make_funnel()
    report = build_report_with_roi(funnel, _standard_roi(), curve=_make_curve())
    json.dumps(report.to_dict())


# ---------------------------------------------------------------------------
# text_summary
# ---------------------------------------------------------------------------


def test_text_summary_contains_key_fields():
    funnel = _make_funnel()
    report = build_report_with_roi(funnel, _standard_roi())
    text = report.text_summary()
    assert "report-test" in text
    assert "Iterations" in text
    assert "Cumulative cost" in text
    assert "Breakeven" in text
    assert "ROI" in text.upper()


def test_text_summary_without_roi_does_not_raise():
    funnel = _make_funnel()
    report = build_report(funnel)
    text = report.text_summary()
    assert "report-test" in text


def test_text_summary_cost_curve_shows_all_iterations():
    funnel = _make_funnel(n_iters=3)
    report = build_report(funnel)
    text = report.text_summary()
    for i in (1, 2, 3):
        assert f"Iter  {i}" in text or f"Iter {i:2d}" in text or f"Iter {i}" in text


# ---------------------------------------------------------------------------
# Integration: build from price schedule → report
# ---------------------------------------------------------------------------


def test_integration_schedule_to_report():
    sched = PriceSchedule(
        process_id="additive_3d_print",
        material_id="pla-filament",
        breaks=(
            QuantityBreak(moq=1,   unit_price_usd=18.00),
            QuantityBreak(moq=10,  unit_price_usd=12.50),
            QuantityBreak(moq=50,  unit_price_usd=9.00),
            QuantityBreak(moq=200, unit_price_usd=6.75),
        ),
    )
    funnel, curve = build_funnel_from_price_schedule(
        sched,
        iteration_quantities=[1, 10, 50, 200],
        name="fdm-bracket-v1",
    )
    roi_input = ROIInput(
        development_cost_usd=funnel.cumulative_cost_usd(),
        unit_manufacturing_cost_usd=sched.unit_price_at(200),
        unit_sale_price_usd=25.0,
        target_volume=500,
    )
    report = build_report_with_roi(funnel, roi_input, curve=curve)

    # Report is complete
    assert report.iteration_count == 4
    assert report.roi is not None
    assert report.roi.viable is True
    assert report.curve_stats is not None
    # curve_stats tracks unit-price reduction (deflationary); funnel tracks total-per-iteration cost
    # (which grows when quantity increases), so use curve_stats for unit-price reduction check
    assert report.curve_stats["reduction_fraction"] == pytest.approx((18 - 6.75) / 18, rel=1e-5)

    # JSON round-trip
    payload = json.loads(report.to_json())
    assert payload["name"] == "fdm-bracket-v1"
    assert payload["roi"]["viable"] is True
