"""Code-CAD Arena time-budget sweep tests (#430)."""

from __future__ import annotations

import json

import pytest

from makerbench.code_cad_time_sweep import (
    SCHEMA,
    SINGLE_HUMAN_TESTER_CONFOUND,
    TimeSweepObservation,
    analyze_time_budget_sweep,
)


def _obs(
    entrant_id: str,
    entrant_type: str,
    budget: float,
    elo: float,
    pass_rate: float,
) -> TimeSweepObservation:
    return TimeSweepObservation(
        entrant_id=entrant_id,
        entrant_type=entrant_type,
        budget_minutes=budget,
        subjective_elo=elo,
        objective_pass_rate=pass_rate,
    )


def _sample_rows() -> list[TimeSweepObservation]:
    return [
        _obs("ai:gpt", "ai_solo", 2, 1010, 0.40),
        _obs("ai:gpt", "ai_solo", 5, 1030, 0.52),
        _obs("ai:gpt", "ai_solo", 10, 1033, 0.54),
        _obs("human:ada", "human_solo", 2, 980, 0.30),
        _obs("human:ada", "human_solo", 5, 1040, 0.56),
        _obs("human:ada", "human_solo", 10, 1110, 0.70),
        _obs("hybrid:ada-gpt", "human_ai", 2, 1000, 0.36),
        _obs("hybrid:ada-gpt", "human_ai", 5, 1065, 0.60),
        _obs("hybrid:ada-gpt", "human_ai", 10, 1120, 0.72),
    ]


def test_observation_rejects_invalid_budget_and_pass_rate():
    with pytest.raises(ValueError, match="budget_minutes"):
        _obs("ai:gpt", "ai_solo", 0, 1000, 0.5)
    with pytest.raises(ValueError, match="objective_pass_rate"):
        _obs("ai:gpt", "ai_solo", 2, 1000, 1.5)


def test_configurable_budget_and_entrant_filters():
    report = analyze_time_budget_sweep(
        _sample_rows(),
        budgets_minutes=[5, 10],
        entrant_types=["ai_solo", "human_ai"],
    )
    assert report.schema == SCHEMA
    assert report.budgets_minutes == (5.0, 10.0)
    assert report.entrant_types == ("ai_solo", "human_ai")
    assert {(p.entrant_type, p.budget_minutes) for p in report.curves} == {
        ("ai_solo", 5.0),
        ("ai_solo", 10.0),
        ("human_ai", 5.0),
        ("human_ai", 10.0),
    }


def test_curve_points_average_replicates_and_track_entrant_ids():
    rows = _sample_rows() + [
        _obs("ai:claude", "ai_solo", 5, 1050, 0.58),
    ]
    report = analyze_time_budget_sweep(rows)
    ai_five = next(
        point for point in report.curves
        if point.entrant_type == "ai_solo" and point.budget_minutes == 5.0
    )
    assert ai_five.subjective_elo == pytest.approx(1040.0)
    assert ai_five.objective_pass_rate == pytest.approx(0.55)
    assert ai_five.observation_count == 2
    assert ai_five.entrant_ids == ("ai:claude", "ai:gpt")


def test_quality_vs_time_curves_emit_subjective_and_objective_metrics():
    report = analyze_time_budget_sweep(_sample_rows())
    hybrid = [
        point for point in report.curves
        if point.entrant_type == "human_ai"
    ]
    assert [point.budget_minutes for point in hybrid] == [2.0, 5.0, 10.0]
    assert [point.subjective_elo for point in hybrid] == [1000, 1065, 1120]
    assert [point.objective_pass_rate for point in hybrid] == [0.36, 0.60, 0.72]


def test_crossovers_find_leader_changes_for_both_metrics():
    report = analyze_time_budget_sweep(_sample_rows())
    crossovers = {
        (
            c.metric,
            c.previous_budget_minutes,
            c.budget_minutes,
            c.leader_before,
            c.leader_after,
        )
        for c in report.crossovers
    }
    assert ("subjective_elo", 2.0, 5.0, "ai_solo", "human_ai") in crossovers
    assert ("objective_pass_rate", 2.0, 5.0, "ai_solo", "human_ai") in crossovers


def test_diminishing_returns_use_configurable_thresholds():
    report = analyze_time_budget_sweep(
        _sample_rows(),
        min_subjective_elo_gain_per_minute=1.0,
        min_objective_pass_rate_gain_per_minute=0.005,
    )
    diminishing = {
        (d.metric, d.entrant_type, d.start_budget_minutes, d.end_budget_minutes)
        for d in report.diminishing_returns
    }
    assert ("subjective_elo", "ai_solo", 5.0, 10.0) in diminishing
    assert ("objective_pass_rate", "ai_solo", 5.0, 10.0) in diminishing
    assert ("subjective_elo", "human_ai", 5.0, 10.0) not in diminishing


def test_report_explicitly_notes_single_human_tester_confound():
    report = analyze_time_budget_sweep(_sample_rows())
    assert report.confounds == (SINGLE_HUMAN_TESTER_CONFOUND,)
    assert "single tester" in report.confounds[0]
    assert "population-level human baselines" in report.confounds[0]


def test_report_to_dict_is_json_serializable():
    report = analyze_time_budget_sweep(_sample_rows())
    payload = report.to_dict()
    assert payload["schema"] == SCHEMA
    encoded = json.dumps(payload, sort_keys=True)
    assert "human_ai" in encoded
    assert "diminishing_returns" in encoded


def test_empty_filtered_sweep_raises():
    with pytest.raises(ValueError, match="at least one observation"):
        analyze_time_budget_sweep(_sample_rows(), entrant_types=["missing"])
