"""Planted off-track rows (Blender mesh, external STEP) through the publication/scoring scripts.

Each test plants one tagged row beside a normal one in the script's real input and asserts the
script's result equals the result without it. Fixtures are reused from each script's own tests.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import test_frontier_vote as fv
import test_render_gallery as rg
import test_rescore_min_wall as rm
import test_studio_repeat_renders as sr

ROOT = Path(__file__).resolve().parents[1]
TAGS = {
    "blender": lambda t: t.__setitem__("backend", "blender"),
    "external": lambda t: t.update(entrant_type="external_step", modality="external_step",
                                   track="external_step"),
}


@pytest.fixture(params=sorted(TAGS))
def tag(request):
    return TAGS[request.param]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"iso_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _edit_log(path: Path, fn) -> None:
    log = json.loads(path.read_text())
    fn(log)
    path.write_text(json.dumps(log))


def test_render_gallery_skips_off_track_rows(tmp_path, tag):
    run = rg._make_run(tmp_path, "r1", ["a", "b"])
    clean, _ = rg.gallery.load_designs(run)
    _edit_log(run / "run_log.json", lambda log: tag(log["trials"][1]))
    designs, _ = rg.gallery.load_designs(run)
    assert len(designs) == len(clean) - 1


def test_rescore_min_wall_skips_off_track_rows(tmp_path, tag):
    run_dir = tmp_path / "runs" / "r1"
    rm._stage_run(run_dir)
    registry = {"instruments": [{"id": "boxolin", "task_brief": "a box", "min_bodies": 1,
                                 "envelope_mm": [200, 200, 200], "min_wall_mm": 1.0}]}
    script = rm._script()
    jobs, passthrough = script.collect_jobs("demo", run_dir, registry)
    assert len(jobs) + len(passthrough) == 3

    def plant(log):
        tag(log["trials"][1])  # the thin-sheet trial would otherwise pull the aggregate down

    _edit_log(run_dir / "run_log.json", plant)
    jobs, passthrough = script.collect_jobs("demo", run_dir, registry)
    assert len(jobs) + len(passthrough) == 2
    assert "boxolin__seed1__rep0__m" not in {j[4]["trial_id"] for j in jobs}


def test_regrade_scoreline_queue_and_output_exclude_off_track_rows(tmp_path, tag):
    script = _load("regrade_scoreline")
    run = tmp_path / "run"
    render = run / "render" / "boxolin__seed0__rep0__m"
    render.mkdir(parents=True)
    (render / "output.stl").write_bytes(b"solid")
    (render / "preview.png").write_bytes(b"\x89PNG\r\n")
    (render / "input.scad").write_text("cube(1);\n")
    trial = {"trial_id": "boxolin__seed0__rep0__m", "instrument_id": "boxolin", "model_id": "m",
             "seed": 0, "status": "scored", "result": {"backend": "openscad"}}
    other = {**trial, "trial_id": "boxolin__seed1__rep0__m", "seed": 1}
    tag(other)
    log = {"config": {"backend": "openscad"}, "trials": [trial, other]}
    registry = {"instruments": [{"id": "boxolin", "min_bodies": 1, "envelope_mm": [200, 200, 200]}]}
    ids = [job[0] for job in script.collect_jobs(run, log, registry)]
    assert ids == ["boxolin__seed0__rep0__m"]


def test_studio_repeat_importer_refuses_off_track_rows(tmp_path, tag):
    root, model, backends = sr.recorded.__wrapped__(tmp_path)
    for run in (model, *backends.values()):
        _edit_log(run / "run_log.json", lambda log: tag(log["trials"][0]))
    with pytest.raises(Exception):  # the tagged expected cell is gone: refused, not published
        sr.importer.prepare(root, model, backends)


def test_frontier_vote_drops_off_track_candidate(tmp_path, tag):
    source = fv.fixture_runs(tmp_path)
    log_path = source / "round1" / "SECRET_MODEL_A" / "run_log.json"
    _edit_log(log_path, lambda log: tag(log["trials"][0]))
    queue = fv.vote.prepare(source, tmp_path / "runs/votes", voter="synthetic-voter", workspace=tmp_path)
    assert len(queue.items) == 9  # round 1 has no pair left: one candidate was off-track
    assert 1 not in {item.meta["round"] for item in queue.items}
