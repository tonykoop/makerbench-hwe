"""Tests for #635 cross-tier scoreline comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench import code_cad_tier_comparison as compare


def _write_run(run_dir: Path, *, model_ids: list[str], trials: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    log = {
        "schema": "makerbench-code-cad-orchestration-v1",
        "config": {"model_ids": model_ids},
        "trials": trials,
    }
    (run_dir / "run_log.json").write_text(json.dumps(log), encoding="utf-8")


def _trial(entrant: str, rate: float, status: str = "scored") -> dict:
    return {
        "trial_id": f"boxolin__seed0__rep0__{entrant}",
        "instrument_id": "boxolin", "model_id": entrant, "seed": 0, "rep": 0,
        "status": status,
        "result": {"objective": {"objective_pass_rate": rate}},
    }


class TestLoadTierRun:
    def test_loads_objective_scoreline_and_tags_tier(self, tmp_path):
        run_dir = tmp_path / "run-blind"
        _write_run(run_dir, model_ids=["stub-a"], trials=[_trial("stub-a", 0.5)])
        loaded = compare.load_tier_run(run_dir, "blind")
        assert loaded["tier"] == "blind"
        assert loaded["run_dir"] == str(run_dir)
        assert loaded["objective"][0]["entrant"] == "stub-a"
        assert loaded["elo"] is None  # no votes.revealed.jsonl


class TestBuildTierComparison:
    def test_needs_at_least_two_runs(self):
        with pytest.raises(ValueError, match="at least two"):
            compare.build_tier_comparison([{"tier": "blind", "objective": []}])

    def test_rejects_duplicate_tier_tags(self):
        with pytest.raises(ValueError, match="duplicate"):
            compare.build_tier_comparison(
                [{"tier": "blind", "objective": []}, {"tier": "blind", "objective": []}]
            )

    def test_computes_delta_for_entrant_present_in_both_tiers(self):
        summary = compare.build_tier_comparison(
            [
                {
                    "tier": "blind", "run_dir": "runs/a",
                    "objective": [{"entrant": "stub-a", "objective_pass_rate": 0.5, "n_objective_trials": 1}],
                },
                {
                    "tier": "image", "run_dir": "runs/b",
                    "objective": [{"entrant": "stub-a", "objective_pass_rate": 0.9, "n_objective_trials": 1}],
                },
            ]
        )
        assert summary["schema"] == compare.SCHEMA
        assert summary["tiers"] == ["blind", "image"]
        row = summary["rows"][0]
        assert row["entrant"] == "stub-a"
        assert row["tiers"]["blind"]["objective_pass_rate"] == 0.5
        assert row["tiers"]["image"]["objective_pass_rate"] == 0.9
        assert row["objective_delta"] == pytest.approx(0.4)

    def test_entrant_missing_from_one_tier_shown_as_null_not_dropped(self):
        summary = compare.build_tier_comparison(
            [
                {
                    "tier": "blind", "run_dir": "runs/a",
                    "objective": [{"entrant": "stub-a", "objective_pass_rate": 0.5, "n_objective_trials": 1}],
                },
                {
                    "tier": "image", "run_dir": "runs/b",
                    "objective": [{"entrant": "stub-b", "objective_pass_rate": 0.9, "n_objective_trials": 1}],
                },
            ]
        )
        by_entrant = {row["entrant"]: row for row in summary["rows"]}
        assert set(by_entrant) == {"stub-a", "stub-b"}
        assert by_entrant["stub-a"]["tiers"]["image"]["objective_pass_rate"] is None
        assert by_entrant["stub-a"]["objective_delta"] is None
        assert by_entrant["stub-b"]["tiers"]["blind"]["objective_pass_rate"] is None

    def test_rows_sorted_by_largest_delta_first(self):
        summary = compare.build_tier_comparison(
            [
                {
                    "tier": "blind", "run_dir": "a",
                    "objective": [
                        {"entrant": "small-gap", "objective_pass_rate": 0.5, "n_objective_trials": 1},
                        {"entrant": "big-gap", "objective_pass_rate": 0.1, "n_objective_trials": 1},
                    ],
                },
                {
                    "tier": "image", "run_dir": "b",
                    "objective": [
                        {"entrant": "small-gap", "objective_pass_rate": 0.55, "n_objective_trials": 1},
                        {"entrant": "big-gap", "objective_pass_rate": 0.9, "n_objective_trials": 1},
                    ],
                },
            ]
        )
        assert [row["entrant"] for row in summary["rows"]] == ["big-gap", "small-gap"]

    def test_three_way_comparison(self):
        summary = compare.build_tier_comparison(
            [
                {"tier": "blind", "run_dir": "a", "objective": [{"entrant": "e", "objective_pass_rate": 0.2, "n_objective_trials": 1}]},
                {"tier": "packet", "run_dir": "b", "objective": [{"entrant": "e", "objective_pass_rate": 0.6, "n_objective_trials": 1}]},
                {"tier": "repo", "run_dir": "c", "objective": [{"entrant": "e", "objective_pass_rate": 0.4, "n_objective_trials": 1}]},
            ]
        )
        assert summary["tiers"] == ["blind", "packet", "repo"]
        row = summary["rows"][0]
        assert row["objective_delta"] == pytest.approx(0.4)  # 0.6 - 0.2


class TestRenderMarkdownComparison:
    def test_renders_table_with_tier_columns(self):
        summary = compare.build_tier_comparison(
            [
                {"tier": "blind", "run_dir": "a", "objective": [{"entrant": "stub-a", "objective_pass_rate": 0.5, "n_objective_trials": 1}]},
                {"tier": "image", "run_dir": "b", "objective": [{"entrant": "stub-a", "objective_pass_rate": 0.9, "n_objective_trials": 1}]},
            ]
        )
        text = compare.render_markdown_comparison(summary)
        assert "| Entrant | blind | image | Δ (max-min) |" in text
        assert "stub-a" in text
        assert "0.500" in text and "0.900" in text


class TestEndToEndFromRunDirs:
    def test_load_and_compare_two_real_run_dirs(self, tmp_path):
        blind_dir = tmp_path / "run-blind"
        image_dir = tmp_path / "run-image"
        _write_run(blind_dir, model_ids=["stub-a"], trials=[_trial("stub-a", 0.6)])
        _write_run(image_dir, model_ids=["stub-a"], trials=[_trial("stub-a", 1.0)])

        loaded = [
            compare.load_tier_run(blind_dir, "blind"),
            compare.load_tier_run(image_dir, "image"),
        ]
        summary = compare.build_tier_comparison(loaded)
        row = summary["rows"][0]
        assert row["objective_delta"] == pytest.approx(0.4)
        assert summary["run_dirs"] == {"blind": str(blind_dir), "image": str(image_dir)}
