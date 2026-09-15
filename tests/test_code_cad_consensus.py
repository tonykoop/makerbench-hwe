"""Offline consensus@N selector tier (#796)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from typer.testing import CliRunner

from makerbench import code_cad_arena_runner as runner
from makerbench import code_cad_consensus as consensus
from makerbench.cli_arena import arena_app


def _box(extents, offset=(0.0, 0.0, 0.0)) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(offset)
    return mesh


def _central_and_outliers() -> dict[str, trimesh.Trimesh]:
    # Three near-identical 20x10x5 plates (one placed at a different origin,
    # which must not matter) plus two outliers of different shape.
    return {
        "a-plate": _box([20.0, 10.0, 5.0]),
        "b-plate-shifted": _box([20.2, 10.0, 5.0], offset=(100.0, -40.0, 7.0)),
        "c-plate": _box([19.8, 10.1, 5.0]),
        "d-tower": _box([5.0, 5.0, 40.0]),
        "e-sphere": trimesh.creation.icosphere(subdivisions=3, radius=12.0),
    }


def test_outliers_lose_and_a_central_candidate_is_selected():
    selection = consensus.select_consensus_meshes(_central_and_outliers(), samples=1024)

    assert selection.tier == "consensus@5"
    assert selection.selected in {"a-plate", "b-plate-shifted", "c-plate"}
    assert set(selection.ranking[-2:]) == {"d-tower", "e-sphere"}
    means = selection.mean_chamfer_mm
    assert max(means[k] for k in ("a-plate", "b-plate-shifted", "c-plate")) < min(
        means["d-tower"], means["e-sphere"]
    )


def test_scale_is_kept_so_a_wrong_scale_candidate_is_an_outlier():
    meshes = {
        "a": _box([20.0, 10.0, 5.0]),
        "b": _box([20.1, 10.0, 5.0]),
        "c": _box([19.9, 10.0, 5.0]),
        "d-inches": _box([20.0 / 25.4, 10.0 / 25.4, 5.0 / 25.4]),
    }
    assert consensus.select_consensus_meshes(meshes, samples=512).ranking[-1] == "d-inches"


def test_ties_break_deterministically_by_candidate_id():
    cube = _box([10.0, 10.0, 10.0])
    meshes = {name: cube.copy() for name in ("zeta", "alpha", "mid")}

    first = consensus.select_consensus_meshes(meshes, samples=256)
    reversed_order = consensus.select_consensus_meshes(dict(reversed(list(meshes.items()))),
                                                       samples=256)

    assert first.selected == reversed_order.selected == "alpha"
    assert first.ranking == reversed_order.ranking == ["alpha", "mid", "zeta"]


def test_floating_point_noise_does_not_reorder_a_tie():
    points = {"b": np.zeros((4, 3)), "a": np.zeros((4, 3)) + 1e-13, "c": np.zeros((4, 3))}
    assert consensus.select_consensus(points).selected == "a"


@pytest.mark.parametrize("n", [0, 1, 2])
def test_fewer_than_three_candidates_is_refused(n):
    meshes = {f"c{i}": _box([10.0, 10.0, 10.0]) for i in range(n)}
    with pytest.raises(consensus.ConsensusError, match="at least 3 candidates"):
        consensus.select_consensus_meshes(meshes)


def test_invalid_candidate_mesh_is_refused_not_scored():
    broken = _box([10.0, 10.0, 10.0])
    broken = trimesh.Trimesh(vertices=broken.vertices, faces=broken.faces[:-2], process=False)
    meshes = {"a": _box([10, 10, 10]), "b": _box([10, 10, 10]), "broken": broken}
    with pytest.raises(consensus.ConsensusError, match="non-manifold"):
        consensus.select_consensus_meshes(meshes)


# --- run-directory integration -------------------------------------------------------------


def _trial(run_dir: Path, trial_id: str, model_id: str, mesh: trimesh.Trimesh, *, rate: float,
           seed: int, tier: str = "blind", status: str = "scored") -> dict:
    render_dir = run_dir / "render" / trial_id
    render_dir.mkdir(parents=True)
    stl = render_dir / "output.stl"
    png = render_dir / "preview.png"
    mesh.export(stl)
    png.write_bytes(b"\x89PNG fake")
    return {
        "trial_id": trial_id,
        "instrument_id": "plate",
        "model_id": model_id,
        "seed": seed,
        "rep": 0,
        "status": status,
        "result": {
            "trial_id": trial_id,
            "model_id": model_id,
            "instrument_id": "plate",
            "seed": seed,
            "status": status,
            "render_ok": True,
            "artifacts": {"stl_path": stl.as_posix(), "png_path": png.as_posix(), "warnings": []},
            "objective": {"objective_pass_rate": rate, "passed": rate >= 1.0, "sub_scores": {}},
            "context_tier": tier,
        },
    }


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    meshes = _central_and_outliers()
    rates = [1.0, 0.8, 1.0, 0.4, 0.2]
    trials = [
        _trial(run_dir, f"plate__seed{i}__rep0__model-a", "model-a", mesh, rate=rate, seed=i)
        for i, (mesh, rate) in enumerate(zip(meshes.values(), rates))
    ]
    # model-b has only two scored candidates: its group must be refused.
    trials += [
        _trial(run_dir, f"plate__seed{i}__rep0__model-b", "model-b", _box([20, 10, 5]),
               rate=1.0, seed=i)
        for i in range(2)
    ]
    run_log = {"schema": "makerbench-code-cad-orchestration-v1", "config": {}, "trials": trials,
               "summary": {}}
    (run_dir / "run_log.json").write_text(json.dumps(run_log, indent=2), encoding="utf-8")
    return run_dir


def test_report_selects_per_group_and_refuses_small_groups(tmp_path):
    report = consensus.build_consensus_report(_run_dir(tmp_path), samples=512)

    assert report["schema"] == "makerbench-code-cad-consensus-v1"
    (selection,) = report["selections"]
    assert selection["tier"] == "consensus@5"
    assert selection["model_id"] == "model-a" and selection["source_tier"] == "blind"
    assert selection["selected_trial_id"] in {
        "plate__seed0__rep0__model-a", "plate__seed1__rep0__model-a", "plate__seed2__rep0__model-a"
    }
    assert selection["single_shot_mean_objective_pass_rate"] == pytest.approx(0.68)
    (refused,) = report["refused"]
    assert refused["model_id"] == "model-b" and refused["n"] == 2
    # Refused up front, before any mesh is loaded or sampled.
    assert refused["reason"].startswith("needs at least 3 scored candidates")


def test_candidate_outside_the_run_dir_is_skipped(tmp_path):
    run_dir = _run_dir(tmp_path)
    run_log = json.loads((run_dir / "run_log.json").read_text())
    outside = tmp_path / "elsewhere.stl"
    _box([20, 10, 5]).export(outside)
    run_log["trials"][0]["result"]["artifacts"]["stl_path"] = outside.as_posix()
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")

    report = consensus.build_consensus_report(run_dir, samples=256)

    assert {"trial_id": "plate__seed0__rep0__model-a",
            "reason": "stl_path is outside the run directory"} in report["skipped"]
    assert report["selections"][0]["n"] == 4


def test_cli_writes_a_sidecar_and_never_touches_the_run_log(tmp_path):
    run_dir = _run_dir(tmp_path)
    log_path = run_dir / "run_log.json"
    before = hashlib.sha256(log_path.read_bytes()).hexdigest()

    result = CliRunner().invoke(arena_app, ["consensus", "--run-dir", str(run_dir),
                                            "--samples", "256"])

    assert result.exit_code == 0, result.output
    assert hashlib.sha256(log_path.read_bytes()).hexdigest() == before
    sidecar = json.loads((run_dir / "consensus.json").read_text())
    assert sidecar["selections"][0]["tier"] == "consensus@5"
    assert "refused" in result.output and "model-b" in result.output
    assert not (run_dir / "vote_pages").exists()


def test_cli_refuses_when_no_group_has_three_candidates(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trials = [_trial(run_dir, f"t{i}", "model-a", _box([5, 5, 5]), rate=1.0, seed=i)
              for i in range(2)]
    (run_dir / "run_log.json").write_text(json.dumps({"trials": trials}), encoding="utf-8")

    result = CliRunner().invoke(arena_app, ["consensus", "--run-dir", str(run_dir)])

    assert result.exit_code == 1
    assert "needs at least 3" in result.output


def test_cli_refuses_to_overwrite_the_run_log(tmp_path):
    run_dir = _run_dir(tmp_path)
    result = CliRunner().invoke(arena_app, ["consensus", "--run-dir", str(run_dir),
                                            "--out", str(run_dir / "run_log.json")])
    assert result.exit_code == 1
    assert json.loads((run_dir / "run_log.json").read_text())["trials"]


# --- blind-series exclusion ---------------------------------------------------------------


def _log_with_consensus_row(tmp_path: Path) -> dict:
    run_dir = _run_dir(tmp_path)
    run_log = json.loads((run_dir / "run_log.json").read_text())
    leaked = json.loads(json.dumps(run_log["trials"][0]))
    leaked["trial_id"] = "plate__consensus5__model-a"
    leaked["model_id"] = "model-a-consensus"
    leaked["result"]["context_tier"] = "consensus@5"
    leaked["result"]["objective"]["objective_pass_rate"] = 1.0
    run_log["trials"].append(leaked)
    return run_log


def test_consensus_rows_never_enter_blind_vote_queues(tmp_path):
    run_log = _log_with_consensus_row(tmp_path)

    cells = runner.build_vote_candidates(run_log)

    ids = [c.candidate_id for cell in cells.values() for c in cell]
    assert "plate__consensus5__model-a" not in ids
    assert "model-a-consensus" not in {c.model_id for cell in cells.values() for c in cell}
    assert len(ids) == 7


def test_consensus_rows_never_enter_the_single_shot_scoreline(tmp_path):
    run_log = _log_with_consensus_row(tmp_path)

    rows = runner.collect_objective_scoreline(run_log)

    assert [row["entrant"] for row in rows] == ["model-b", "model-a"]
    assert rows[1]["n_objective_trials"] == 5


@pytest.mark.parametrize("where", ["entry.tier", "result.context_tier", "meta.context_tier"])
def test_every_tier_location_is_recognised(where):
    entry: dict = {"result": {}, "meta": {}}
    owner, key = where.split(".")
    (entry if owner == "entry" else entry[owner])[key] = "consensus@3"
    assert runner.is_consensus_row(entry)
    assert not runner.is_consensus_row({"result": {"context_tier": "blind"}})
