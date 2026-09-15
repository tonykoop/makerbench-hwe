"""Arena Studio reads compiled/manifold from the real run-log shape (#720).

Fixtures use the exact trial shapes ``evaluate_objective_trial`` writes, a scored
row and an ``auto_fail`` row, with no synthetic ``grade`` block, which real run
logs never carry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench.arena_studio.service import ArenaStudioService

SUB_SCORE_KEYS = ("renders", "watertight", "nonzero_volume", "fits_envelope", "min_wall",
                  "body_count")


def _scored(trial_id: str, model_id: str, scad: Path, *, watertight: bool) -> dict:
    sub_scores = dict.fromkeys(SUB_SCORE_KEYS, 1.0)
    sub_scores["watertight"] = 1.0 if watertight else 0.0
    rate = sum(sub_scores.values()) / len(sub_scores)
    return {
        "trial_id": trial_id, "model_id": model_id, "instrument_id": "ocarina",
        "seed": 0, "rep": 0, "provider": "stub", "status": "scored", "attempts": 1,
        "result": {
            "schema": "makerbench-code-cad-objective-v1", "trial_id": trial_id,
            "model_id": model_id, "instrument_id": "ocarina", "seed": 0,
            "status": "scored", "failure_stage": None, "render_ok": True,
            "artifacts": {"stl_path": None, "png_path": None, "warnings": []},
            "scad_path": scad.as_posix(),
            "objective": {"passed": rate >= 1.0, "objective_pass_rate": round(rate, 6),
                          "sub_scores": sub_scores,
                          "gate": "makerbench.code_cad_arena_runner.mesh_objective_gate"},
            "error": None,
        },
    }


def _auto_fail(trial_id: str, model_id: str, scad: Path) -> dict:
    return {
        "trial_id": trial_id, "model_id": model_id, "instrument_id": "ocarina",
        "seed": 0, "rep": 0, "provider": "stub", "status": "scored", "attempts": 1,
        "result": {
            "schema": "makerbench-code-cad-objective-v1", "trial_id": trial_id,
            "model_id": model_id, "instrument_id": "ocarina", "seed": 0,
            "status": "auto_fail", "failure_stage": "compile", "render_ok": False,
            "artifacts": {"stl_path": None, "png_path": None, "warnings": []},
            "scad_path": scad.as_posix(),
            "objective": {"passed": False, "objective_pass_rate": 0.0, "sub_scores": {},
                          "gate": None},
            "error": "openscad exited 1",
        },
    }


def _reveal(winner_model: str, loser_model: str) -> dict:
    return {"winner": "left", "instrument_id": "ocarina",
            "reveal": {"left": {"model_id": winner_model}, "right": {"model_id": loser_model}}}


def _write(run_dir: Path, trials: list[dict], reveals: list[dict]) -> Path:
    run_dir.mkdir(parents=True)
    run_log = {"config": {"model_ids": sorted({t["model_id"] for t in trials})},
               "trials": trials}
    assert "grade" not in json.dumps(run_log), "fixture must use the real run-log shape"
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    (run_dir / "votes.revealed.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in reveals), encoding="utf-8")
    return run_dir


@pytest.fixture
def service(tmp_path: Path) -> ArenaStudioService:
    return ArenaStudioService(repo_root=tmp_path)


def _scad(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.scad"
    path.write_text(f"// {name}\n", encoding="utf-8")
    return path


def test_summary_and_report_count_compiled_and_manifold_from_real_trials(service, tmp_path):
    run_dir = _write(tmp_path / "runs" / "code_cad_arena" / "real_shape", [
        _scored("t1", "solid-model", _scad(tmp_path, "a"), watertight=True),
        _scored("t2", "leaky-model", _scad(tmp_path, "b"), watertight=False),
        _auto_fail("t3", "broken-model", _scad(tmp_path, "c")),
    ], [])

    summary = service.get_run_summary(run_dir)

    assert summary["compiled_count"] == 2
    assert summary["manifold_count"] == 1
    report = service.export_report(run_dir)
    assert "- Compiled Models: 2" in report and "- Manifold Meshes: 1" in report


def test_export_winners_prefers_a_compiled_trial_over_a_better_rated_failure(service, tmp_path):
    run_dir = _write(tmp_path / "runs" / "code_cad_arena" / "compiled_first", [
        _auto_fail("t-fav", "favourite-model", _scad(tmp_path, "fav")),
        _scored("t-valid", "valid-model", _scad(tmp_path, "valid"), watertight=True),
    ], [_reveal("favourite-model", "valid-model")] * 3)
    leaderboard = service.get_run_leaderboard(run_dir)["leaderboard"]
    assert leaderboard[0]["entrant"] == "favourite-model", "precondition: the failure out-rates"

    result = service.export_winners(run_dir)

    assert [w["model_id"] for w in result["winners"]] == ["valid-model"]


def test_export_winners_prefers_a_manifold_trial_over_a_better_rated_leaky_one(service, tmp_path):
    run_dir = _write(tmp_path / "runs" / "code_cad_arena" / "manifold_first", [
        _scored("t-leaky", "leaky-model", _scad(tmp_path, "leaky"), watertight=False),
        _scored("t-solid", "solid-model", _scad(tmp_path, "solid"), watertight=True),
    ], [_reveal("leaky-model", "solid-model")] * 3)
    leaderboard = service.get_run_leaderboard(run_dir)["leaderboard"]
    assert leaderboard[0]["entrant"] == "leaky-model", "precondition: the leaky mesh out-rates"

    result = service.export_winners(run_dir)

    assert [w["model_id"] for w in result["winners"]] == ["solid-model"]
