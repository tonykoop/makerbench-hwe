"""scripts/rescore_min_wall.py (epic T2, #979): replay recorded meshes under both estimators."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import trimesh

ROOT = Path(__file__).resolve().parent.parent


def _script():
    spec = importlib.util.spec_from_file_location("mb_rescore_min_wall", ROOT / "scripts" / "rescore_min_wall.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage_run(run_dir: Path) -> None:
    """A recorded run: one scored trial whose mesh is a 5 mm plate with a 0.3 mm blade on
    ~0.03% of its surface (legacy minimum at seed 0 happens to pass it; see the seed table in
    test_min_wall_robust), one 0.4 mm sheet (fails both), and one generation error."""
    plate = trimesh.boolean.union([trimesh.creation.box(extents=[50, 50, 5]),
                                   trimesh.creation.box(extents=[1, 1, 0.3]).apply_translation([25, 0, 0])],
                                  engine="manifold")
    sheet = trimesh.creation.box(extents=[50, 50, 0.4])
    trials = []
    for tid, mesh in (("boxolin__seed0__rep0__m", plate), ("boxolin__seed1__rep0__m", sheet)):
        render = run_dir / "render" / tid
        render.mkdir(parents=True)
        mesh.export((render / "output.stl").as_posix())
        (render / "preview.png").write_bytes(b"\x89PNG\r\n")
        (render / "input.scad").write_text("cube(1);\n")
        trials.append({"trial_id": tid, "model_id": "m", "instrument_id": "boxolin", "seed": int(tid[13]),
                       "status": "scored", "result": {"backend": "openscad", "context_tier": "blind",
                                                      "objective": {"objective_pass_rate": 0.5,
                                                                    "sub_scores": {"min_wall": 0.0}}}})
    trials.append({"trial_id": "boxolin__seed2__rep0__m", "model_id": "m", "instrument_id": "boxolin", "seed": 2,
                   "status": "error", "result": None, "meta": {"context_tier": "blind"}})
    (run_dir / "run_log.json").write_text(json.dumps({"config": {"backend": "openscad"}, "trials": trials}))


def test_rescore_replays_both_estimators_and_passes_failures_through(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    _stage_run(run_dir)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": [{"id": "boxolin", "task_brief": "a box", "min_bodies": 1,
                                                     "envelope_mm": [200, 200, 200], "min_wall_mm": 1.0}]}))
    out_json, out_md = tmp_path / "rescore.json", tmp_path / "rescore.md"
    assert _script().main(["--run", f"demo={run_dir}", "--registry", str(registry), "--workers", "1",
                           "--out-json", str(out_json), "--out-md", str(out_md)]) == 0

    payload = json.loads(out_json.read_text())
    by_id = {t["trial_id"]: t for t in payload["trials"]}
    sheet = by_id["boxolin__seed1__rep0__m"]
    assert sheet["min"]["min_wall"] == 0.0 and sheet["robust-v1"]["min_wall"] == 0.0  # really thin: fails both
    plate = by_id["boxolin__seed0__rep0__m"]
    assert plate["robust-v1"]["min_wall"] == 1.0 and plate["robust-v1"]["min_wall_mm"] >= 4.9
    error = by_id["boxolin__seed2__rep0__m"]
    assert error["regraded"] is False and error["min"]["objective_pass_rate"] == 0.0

    (row,) = payload["rows"]
    assert row["n"] == 3 and row["n_regraded"] == 2 and row["context_tier"] == "blind"
    assert row["recorded"] == round((0.5 + 0.5 + 0.0) / 3, 6)
    assert row["robust-v1"] == round((plate["robust-v1"]["objective_pass_rate"]
                                      + sheet["robust-v1"]["objective_pass_rate"]) / 3, 6)
    assert row["delta"] == round(row["robust-v1"] - row["min"], 6)
    assert "| demo | m | openscad | 3 (2) |" in out_md.read_text()
    # objective metadata only: no local path ever reaches the outputs
    assert str(tmp_path) not in out_json.read_text() and str(tmp_path) not in out_md.read_text()
