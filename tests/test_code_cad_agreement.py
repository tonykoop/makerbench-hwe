"""Tests for Code-CAD Arena dual-scoreline agreement analysis (#427)."""

from pathlib import Path

import pytest

from makerbench import code_cad_agreement as agreement


def test_side_by_side_rankings_and_delta():
    summary = agreement.build_agreement_summary(
        [
            {"entrant": "gpt-5.5", "subjective_elo": 1600, "objective_pass_rate": 0.80},
            {"entrant": "sonnet", "subjective_elo": 1550, "objective_pass_rate": 0.90},
            {"entrant": "gemini", "subjective_elo": 1500, "objective_pass_rate": 0.20},
        ]
    )

    rows = {row["entrant"]: row for row in summary["rankings"]}
    assert summary["schema"] == agreement.SCHEMA
    assert rows["gpt-5.5"]["subjective_rank"] == 1.0
    assert rows["gpt-5.5"]["objective_rank"] == 2.0
    assert rows["gpt-5.5"]["rank_delta"] == 1.0
    assert rows["sonnet"]["subjective_rank"] == 2.0
    assert rows["sonnet"]["objective_rank"] == 1.0


def test_spearman_agreement_perfect_and_inverted():
    aligned = agreement.build_agreement_summary(
        [
            {"entrant": "a", "subjective_elo": 3, "objective_pass_rate": 0.9},
            {"entrant": "b", "subjective_elo": 2, "objective_pass_rate": 0.5},
            {"entrant": "c", "subjective_elo": 1, "objective_pass_rate": 0.1},
        ]
    )
    inverted = agreement.build_agreement_summary(
        [
            {"entrant": "a", "subjective_elo": 3, "objective_pass_rate": 0.1},
            {"entrant": "b", "subjective_elo": 2, "objective_pass_rate": 0.5},
            {"entrant": "c", "subjective_elo": 1, "objective_pass_rate": 0.9},
        ]
    )

    assert aligned["agreement"]["rho"] == 1.0
    assert aligned["agreement"]["interpretation"] == "strong_alignment"
    assert inverted["agreement"]["rho"] == -1.0
    assert inverted["agreement"]["interpretation"] == "inverted_ranking"


def test_tie_aware_average_ranks():
    summary = agreement.build_agreement_summary(
        [
            {"entrant": "a", "subjective_elo": 1500, "objective_pass_rate": 0.5},
            {"entrant": "b", "subjective_elo": 1500, "objective_pass_rate": 0.4},
            {"entrant": "c", "subjective_elo": 1400, "objective_pass_rate": 0.3},
        ]
    )

    rows = {row["entrant"]: row for row in summary["rankings"]}
    assert rows["a"]["subjective_rank"] == 1.5
    assert rows["b"]["subjective_rank"] == 1.5
    assert rows["c"]["subjective_rank"] == 3.0


def test_missing_scorelines_remain_exported_but_excluded_from_agreement():
    summary = agreement.build_agreement_summary(
        [
            {"entrant": "a", "subjective_elo": 1600, "objective_pass_rate": 0.8},
            {"entrant": "b", "subjective_elo": 1500},
            {"entrant": "c", "subjective_elo": 1400, "objective_pass_rate": 0.2},
        ]
    )

    rows = {row["entrant"]: row for row in summary["rankings"]}
    assert rows["b"]["objective_rank"] is None
    assert summary["agreement"]["n"] == 2
    assert summary["agreement"]["entrants"] == ["a", "c"]


def test_markdown_summary_is_exportable():
    summary = agreement.build_agreement_summary(
        [
            {"entrant": "a", "subjective_elo": 1600, "objective_pass_rate": 0.8},
            {"entrant": "b", "subjective_elo": 1500, "objective_pass_rate": 0.2},
        ]
    )

    text = agreement.render_markdown_summary(summary)
    assert "Spearman rank correlation" in text
    assert "| Entrant | Subjective Elo | Subjective rank | Objective pass-rate |" in text
    assert "| a | 1600" in text


def test_invalid_objective_rate_rejected():
    with pytest.raises(ValueError, match="objective_pass_rate"):
        agreement.build_agreement_summary(
            [{"entrant": "a", "subjective_elo": 1500, "objective_pass_rate": 1.1}]
        )


def test_doc_defines_metric_and_confound():
    doc = Path(__file__).resolve().parents[1] / "docs" / "CODE_CAD_AGREEMENT.md"
    text = doc.read_text(encoding="utf-8")
    assert "Spearman rank correlation" in text
    assert "aesthetics-vs-manufacturability" in text
