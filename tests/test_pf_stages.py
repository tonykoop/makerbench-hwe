"""Tests for prototyping_funnel.stages — stage model and deflationary curve.

Covers: PrototypingFunnel, IterationRecord, StageResult, STAGE_ORDER.
Epic #240, story slice: stage model (concept → DFM → quote → prototype → validation).
"""

from __future__ import annotations

import json

import pytest

from prototyping_funnel.stages import (
    STAGE_ORDER,
    IterationRecord,
    PrototypingFunnel,
    StageResult,
)


# ---------------------------------------------------------------------------
# StageResult
# ---------------------------------------------------------------------------


def test_stage_result_basic_pass():
    s = StageResult("concept", passed=True, cost_usd=120.0, lead_time_days=2.5)
    assert s.stage == "concept"
    assert s.passed is True
    assert s.cost_usd == 120.0
    assert s.lead_time_days == 2.5


def test_stage_result_rejects_negative_cost():
    with pytest.raises(ValueError, match="cost_usd"):
        StageResult("concept", passed=True, cost_usd=-1.0)


def test_stage_result_rejects_negative_lead_time():
    with pytest.raises(ValueError, match="lead_time_days"):
        StageResult("concept", passed=True, lead_time_days=-0.5)


def test_stage_result_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unknown stage"):
        StageResult("fabrication", passed=True)  # type: ignore[arg-type]


def test_stage_result_as_dict_is_json_serializable():
    s = StageResult("dfm", passed=False, cost_usd=0.0, lead_time_days=1.0, notes=["flag: thin wall"])
    d = s.as_dict()
    json.dumps(d)  # must not raise
    assert d["stage"] == "dfm"
    assert d["passed"] is False
    assert d["notes"] == ["flag: thin wall"]


# ---------------------------------------------------------------------------
# IterationRecord
# ---------------------------------------------------------------------------


def _make_iteration(n: int, *, total_cost: float = 500.0, total_days: float = 10.0) -> IterationRecord:
    """Build a simple concept+prototype iteration."""
    half = total_cost / 2
    half_days = total_days / 2
    return IterationRecord(
        iteration=n,
        stages=(
            StageResult("concept", passed=True, cost_usd=half, lead_time_days=half_days),
            StageResult("prototype", passed=True, cost_usd=half, lead_time_days=half_days),
        ),
    )


def test_iteration_record_computes_totals():
    rec = _make_iteration(1, total_cost=300.0, total_days=8.0)
    assert rec.total_cost_usd == 300.0
    assert rec.total_lead_time_days == 8.0


def test_iteration_record_reached_validation_false_for_prototype():
    rec = _make_iteration(1)
    assert rec.reached_validation is False


def test_iteration_record_reached_validation_true():
    rec = IterationRecord(
        iteration=1,
        stages=(
            StageResult("concept", passed=True, cost_usd=100),
            StageResult("validation", passed=True, cost_usd=50),
        ),
    )
    assert rec.reached_validation is True


def test_iteration_record_reached_validation_false_when_validation_fails():
    rec = IterationRecord(
        iteration=1,
        stages=(
            StageResult("concept", passed=True, cost_usd=100),
            StageResult("validation", passed=False, cost_usd=50),
        ),
    )
    assert rec.reached_validation is False


def test_iteration_record_rejects_iteration_zero():
    with pytest.raises(ValueError, match="iteration must be >= 1"):
        IterationRecord(iteration=0, stages=(StageResult("concept", passed=True),))


def test_iteration_record_rejects_out_of_order_stages():
    with pytest.raises(ValueError, match="forward order"):
        IterationRecord(
            iteration=1,
            stages=(
                StageResult("prototype", passed=True),
                StageResult("concept", passed=True),
            ),
        )


def test_iteration_record_rejects_empty_stages():
    with pytest.raises(ValueError, match="at least one"):
        IterationRecord(iteration=1, stages=())


def test_iteration_record_as_dict_is_json_serializable():
    rec = _make_iteration(1, total_cost=400.0)
    d = rec.as_dict()
    json.dumps(d)
    assert d["iteration"] == 1
    assert d["total_cost_usd"] == 400.0
    assert len(d["stages"]) == 2


def test_stage_order_covers_all_five_stages():
    assert STAGE_ORDER == ("concept", "dfm", "quote", "prototype", "validation")


# ---------------------------------------------------------------------------
# PrototypingFunnel
# ---------------------------------------------------------------------------


def _three_iteration_funnel() -> PrototypingFunnel:
    funnel = PrototypingFunnel("test-bracket")
    costs = [600.0, 350.0, 220.0]
    days = [12.0, 9.0, 7.0]
    for i, (cost, d) in enumerate(zip(costs, days), 1):
        funnel.add_iteration(_make_iteration(i, total_cost=cost, total_days=d))
    return funnel


def test_funnel_accumulates_iterations():
    funnel = _three_iteration_funnel()
    assert len(funnel.iterations) == 3


def test_funnel_cumulative_cost():
    funnel = _three_iteration_funnel()
    assert funnel.cumulative_cost_usd() == 600.0 + 350.0 + 220.0


def test_funnel_cumulative_lead_time():
    funnel = _three_iteration_funnel()
    assert funnel.cumulative_lead_time_days() == 12.0 + 9.0 + 7.0


def test_funnel_cost_reduction_fraction():
    funnel = _three_iteration_funnel()
    # (600 - 220) / 600 = 0.6333...
    expected = (600.0 - 220.0) / 600.0
    assert abs(funnel.cost_reduction_fraction() - expected) < 1e-6


def test_funnel_lead_time_reduction_fraction():
    funnel = _three_iteration_funnel()
    expected = (12.0 - 7.0) / 12.0
    assert abs(funnel.lead_time_reduction_fraction() - expected) < 1e-6


def test_funnel_cost_reduction_none_for_single_iteration():
    funnel = PrototypingFunnel()
    funnel.add_iteration(_make_iteration(1))
    assert funnel.cost_reduction_fraction() is None


def test_funnel_validation_rate_zero_when_no_validation_reached():
    funnel = _three_iteration_funnel()
    assert funnel.validation_rate() == 0.0


def test_funnel_validation_rate_nonzero():
    funnel = PrototypingFunnel("val-test")
    funnel.add_iteration(IterationRecord(
        iteration=1,
        stages=(
            StageResult("concept", passed=True, cost_usd=200),
            StageResult("validation", passed=True, cost_usd=80),
        ),
    ))
    funnel.add_iteration(_make_iteration(2))   # no validation
    assert funnel.validation_rate() == pytest.approx(0.5)


def test_funnel_rejects_non_sequential_iterations():
    funnel = PrototypingFunnel()
    funnel.add_iteration(_make_iteration(1))
    with pytest.raises(ValueError, match="sequential"):
        funnel.add_iteration(_make_iteration(3))   # skipped #2


def test_funnel_curve_returns_list_of_dicts():
    funnel = _three_iteration_funnel()
    curve = funnel.curve()
    assert len(curve) == 3
    assert curve[0]["iteration"] == 1
    assert curve[0]["total_cost_usd"] == 600.0
    assert curve[2]["total_cost_usd"] == 220.0
    # Deflationary: each entry should be cheaper than the previous
    costs = [c["total_cost_usd"] for c in curve]
    assert costs == sorted(costs, reverse=True)


def test_funnel_summary_is_json_serializable():
    funnel = _three_iteration_funnel()
    summary = funnel.summary()
    json.dumps(summary)
    assert summary["iteration_count"] == 3
    assert "cost_reduction_fraction" in summary
    assert "curve" in summary


def test_funnel_summary_name_propagates():
    funnel = PrototypingFunnel("motor-bracket-v2")
    funnel.add_iteration(_make_iteration(1))
    assert funnel.summary()["name"] == "motor-bracket-v2"
