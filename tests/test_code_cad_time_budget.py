"""Tests for Code-CAD Arena time-budget sweep analysis (#430)."""

from pathlib import Path

import pytest

from makerbench import code_cad_time_budget as tb


def _obs(entrant, budget, subjective, objective, n_trials=1):
    return tb.BudgetObservation(
        entrant_type=entrant,
        budget_minutes=budget,
        subjective_elo=subjective,
        objective_pass_rate=objective,
        n_trials=n_trials,
    )


def test_configurable_budget_sweep_builds_quality_curves():
    config = tb.TimeBudgetConfig(
        budgets_minutes=(2.0, 5.0, 10.0),
        entrant_types=("ai-solo", "human-solo", "human+ai"),
    )
    report = tb.analyze_time_budget_sweep(
        [
            _obs("ai-solo", 2, 1510, 0.40, n_trials=3),
            _obs("ai-solo", 5, 1530, 0.55, n_trials=3),
            _obs("human-solo", 2, 1490, 0.30, n_trials=2),
        ],
        config=config,
    )

    assert report["schema"] == tb.SCHEMA
    assert report["config"]["budgets_minutes"] == [2.0, 5.0, 10.0]
    curves = {curve["entrant_type"]: curve["points"] for curve in report["curves"]}
    assert [point["budget_minutes"] for point in curves["ai-solo"]] == [2.0, 5.0]
    assert curves["ai-solo"][0]["subjective_elo"] == 1510.0
    assert curves["ai-solo"][0]["objective_pass_rate"] == 0.4
    assert curves["ai-solo"][0]["n_trials"] == 3
    assert curves["human+ai"] == []


def test_duplicate_observations_are_averaged_per_budget():
    report = tb.analyze_time_budget_sweep(
        [
            _obs("human+ai", 5, 1540, 0.70, n_trials=2),
            _obs("human+ai", 5, 1560, 0.80, n_trials=4),
        ]
    )

    point = next(curve for curve in report["curves"] if curve["entrant_type"] == "human+ai")[
        "points"
    ][0]
    assert point["subjective_elo"] == 1550.0
    assert point["objective_pass_rate"] == 0.75
    assert point["n_observations"] == 2
    assert point["n_trials"] == 6


def test_crossovers_detect_leader_changes_for_both_scorelines():
    report = tb.analyze_time_budget_sweep(
        [
            _obs("ai-solo", 2, 1540, 0.70),
            _obs("human+ai", 2, 1500, 0.55),
            _obs("ai-solo", 5, 1550, 0.72),
            _obs("human+ai", 5, 1570, 0.75),
        ]
    )

    by_metric = {item["metric"]: item for item in report["crossovers"]}
    assert by_metric["subjective_elo"]["between_budget_minutes"] == [2.0, 5.0]
    assert by_metric["subjective_elo"]["from_leader"] == "ai-solo"
    assert by_metric["subjective_elo"]["to_leader"] == "human+ai"
    assert by_metric["objective_pass_rate"]["to_leader"] == "human+ai"


def test_diminishing_returns_identify_flattening_curve():
    config = tb.TimeBudgetConfig(objective_pass_rate_min_gain_per_minute=0.02)
    report = tb.analyze_time_budget_sweep(
        [
            _obs("human-solo", 2, None, 0.20),
            _obs("human-solo", 5, None, 0.50),
            _obs("human-solo", 10, None, 0.55),
        ],
        config=config,
    )

    diminishing = report["diminishing_returns"]
    assert diminishing == [
        {
            "entrant_type": "human-solo",
            "metric": "objective_pass_rate",
            "after_budget_minutes": 5.0,
            "next_budget_minutes": 10.0,
            "gain_per_minute": 0.01,
            "threshold": 0.02,
        }
    ]


def test_invalid_rows_are_rejected():
    with pytest.raises(ValueError, match="objective_pass_rate"):
        tb.analyze_time_budget_sweep(
            [{"entrant_type": "ai-solo", "budget_minutes": 2, "objective_pass_rate": 1.2}]
        )

    with pytest.raises(ValueError, match="budget_minutes"):
        tb.analyze_time_budget_sweep([{"entrant_type": "ai-solo", "budget_minutes": 0}])


def test_doc_notes_single_human_tester_confound():
    doc = Path(__file__).resolve().parents[1] / "docs" / "CODE_CAD_TIME_BUDGET.md"
    text = doc.read_text(encoding="utf-8")
    assert "single-human-tester" in text
    assert "active authoring time" in text
