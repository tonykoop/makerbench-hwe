# ruff: noqa: F811  (the `night` fixture is shared from test_nightly_revisions)
"""#1116: the production revision handler and the `arena overnight` entry point (fakes only)."""

import json
from pathlib import Path

import trimesh
from typer.testing import CliRunner

from makerbench import code_cad_arena_runner as arena_runner
from makerbench import code_cad_providers as providers
from makerbench import nightly_revisions as rev
from makerbench.cli import app
from makerbench.code_cad_objective import RenderArtifacts
from makerbench.nightly_cad import NightlyEntrant, NightlyExecutor, NightlyJob
from makerbench.nightly_revise import RevisionUnsupported, make_revision_handler
from makerbench.run_log_io import atomic_write_json

from test_nightly_cad import _synthetic_handler  # noqa: E402
from test_nightly_revisions import ENTRANT, NOW, _handlers, _job, _jobs, _write_queue, night  # noqa: E402,F401
import pytest  # noqa: E402

runner = CliRunner()
SOURCE = "cube([40, 20, 10]); // original body\n"


class FakeGenerator:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return "cube([40, 20, 14]); // deeper\n"


def fake_compiler(_backend):
    def compile_(scad_path, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        stl = out_dir / "output.stl"
        trimesh.creation.box(extents=[30, 20, 10]).export(stl)
        png = out_dir / "preview.png"
        png.write_bytes(b"\x89PNG\r\n")
        return RenderArtifacts(stl_path=stl, png_path=png)

    return compile_


def _plant_source(night, *, with_turn_record=False):
    """Give the finished fake night a real stored candidate (+ optionally a per-turn record)."""
    run_dir = night.runs / night.run_id
    trial_id = f"sambuca__seed0__rep0__{ENTRANT}"
    scad = run_dir / "gen" / trial_id / "candidate.scad"
    scad.parent.mkdir(parents=True, exist_ok=True)
    scad.write_text(SOURCE)
    turn_scad = run_dir / "gen" / trial_id / "turn3.scad"
    turn_scad.write_text("cube([1, 1, 1]); // turn three\n")
    log_path = run_dir / "run_log.json"
    log = json.loads(log_path.read_text())
    row = next(r for r in log["trials"] if r["trial_id"] == trial_id)
    row["result"]["gen"] = {"scad_path": scad.as_posix()}
    if with_turn_record:
        row["result"]["turns"] = [{"turn": 3, "passed": 5, "total": 5, "source_path": turn_scad.as_posix()}]
    atomic_write_json(log_path, log)
    return trial_id


def _patch_providers(monkeypatch, generator):
    monkeypatch.setattr(providers, "resolve_generator", lambda *a, **k: generator)
    monkeypatch.setattr(arena_runner, "compiler_for_backend", fake_compiler)


def test_handler_continues_the_stored_candidate_and_ingests_a_scored_trial(night, monkeypatch):
    _plant_source(night)
    gen = FakeGenerator()
    _patch_providers(monkeypatch, gen)
    path = _write_queue(night, _job(night, feedback="make the soundbox deeper"))
    result = NightlyExecutor(
        queue_path=night.tmp / "queue.json", registry_path=night.tmp / "registry.json", output_root=night.runs,
        now=lambda: NOW, runs_root=night.runs, handlers={"revision": make_revision_handler()},
    ).run()

    (request,) = gen.requests
    assert "original body" in request.prompt and "make the soundbox deeper" in request.prompt
    assert "turn 3" in request.prompt and "2 further turns" in request.prompt
    assert _jobs(path)["sambuca-night-revise-codex-openscad"]["status"] == "done"
    new_log = json.loads((Path(result["run_dir"]) / "run_log.json").read_text())
    (row,) = new_log["trials"]
    assert row["model_id"] == ENTRANT and row["status"] == "scored"
    provenance = json.loads(Path(row["result"]["gen"]["provenance_path"]).read_text())
    assert provenance["revision_of"]["turn_source"] == "stored-candidate"
    assert provenance["revision_of"]["run_id"] == night.run_id and provenance["revision_of"]["extra_turns"] == 2
    assert (Path(row["result"]["gen"]["scad_path"])).read_text().startswith("cube([40, 20, 14])")


def test_a_recorded_turn_source_is_preferred_over_the_stored_candidate(night, monkeypatch):
    _plant_source(night, with_turn_record=True)
    gen = FakeGenerator()
    _patch_providers(monkeypatch, gen)
    _write_queue(night, _job(night))
    NightlyExecutor(
        queue_path=night.tmp / "queue.json", registry_path=night.tmp / "registry.json", output_root=night.runs,
        now=lambda: NOW, runs_root=night.runs, handlers={"revision": make_revision_handler()},
    ).run()
    assert "turn three" in gen.requests[0].prompt and "original body" not in gen.requests[0].prompt


def test_overnight_cli_consumes_a_revision_end_to_end(night, monkeypatch):
    """The production entry point (real constructor, real handler) with fake provider + compiler."""
    _plant_source(night)
    gen = FakeGenerator()
    _patch_providers(monkeypatch, gen)
    path = _write_queue(night, _job(night))
    args = ["arena", "overnight", "--queue", str(night.tmp / "queue.json"), "--output-root", str(night.runs),
            "--registry", str(night.tmp / "registry.json"), "--lease-path", str(night.tmp / "lease.lock")]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "queue-empty" not in result.output and len(gen.requests) == 1
    job = _jobs(path)["sambuca-night-revise-codex-openscad"]
    assert job["status"] == "done" and job["new_run"]["run_id"] in result.output

    again = runner.invoke(app, args)
    assert "queue-empty" in again.output and len(gen.requests) == 1  # consumed once

    _write_queue(night, _job(night, feedback="again"))
    off = runner.invoke(app, [*args, "--no-revisions"])
    assert "queue-empty" in off.output and len(gen.requests) == 1  # opt-out leaves it queued
    assert _jobs(path)["sambuca-night-revise-codex-openscad"]["status"] == "queued"


@pytest.mark.parametrize(
    ("entrant", "message"),
    [
        (NightlyEntrant(entrant_id="x", kind="cadam", model_id="m"), "not supported"),
        (NightlyEntrant(entrant_id="x", kind="arena", model_id="m", backend="cadquery"), "not supported"),
    ],
)
def test_unsupported_entrants_fail_the_request_with_a_reason(tmp_path, entrant, message):
    job = NightlyJob(job_id="j", instrument_id="sambuca", reference_image="", entrants=[entrant],
                     revision={"source_run_dir": tmp_path.as_posix(), "trial_id": "t", "source_run_id": "r",
                               "feedback": "f", "extra_turns": 1})
    with pytest.raises(RevisionUnsupported, match=message):
        make_revision_handler()(job, entrant, tmp_path, {})


def test_missing_source_run_or_design_is_reported(tmp_path):
    entrant = NightlyEntrant(entrant_id="x", kind="arena", model_id="m")
    job = NightlyJob(job_id="j", instrument_id="sambuca", reference_image="", entrants=[entrant],
                     revision={"source_run_dir": tmp_path.as_posix(), "trial_id": "t", "source_run_id": "r",
                               "feedback": "f", "extra_turns": 1})
    with pytest.raises(RevisionUnsupported, match="run_log.json"):
        make_revision_handler()(job, entrant, tmp_path, {})
    atomic_write_json(tmp_path / "run_log.json", {"trials": []})
    with pytest.raises(RevisionUnsupported, match="not in the source run"):
        make_revision_handler()(job, entrant, tmp_path, {})


# ---- confinement of derived paths ---------------------------------------------------------


@pytest.mark.parametrize("job_id", ["x/../../escaped", "../up", "a b", "/abs", ".hidden", "a..b", "x\\y", ""])
def test_unsafe_job_ids_never_become_folders(night, job_id):
    bad = {**_job(night), "job_id": job_id}
    good = {**_job(night, trial="other"), "job_id": "good"}
    path = _write_queue(night, bad, good)
    before = {p.name for p in night.tmp.iterdir()}
    night.executor(handlers=_handlers(revision=_synthetic_handler), runs_root=night.runs).run()
    jobs = {j["job_id"]: j for j in json.loads(path.read_text())["jobs"]}
    assert jobs[job_id]["status"] == "failed" and "malformed" in jobs[job_id]["error"]
    assert {p.name for p in night.tmp.iterdir()} == before  # nothing written outside runs/
    assert not (night.tmp / "escaped").exists() and not (night.runs.parent / "escaped").exists()


def test_a_symlinked_destination_run_folder_is_refused_before_any_write(night):
    outside = night.tmp / "elsewhere"
    outside.mkdir()
    stamp = NOW.strftime("%Y%m%dT%H%M%SZ")
    (night.runs / f"{stamp}-sambuca-night-revise-{ENTRANT}").symlink_to(outside, target_is_directory=True)
    path = _write_queue(night, _job(night))
    with pytest.raises(ValueError, match="symlink"):
        night.executor(handlers=_handlers(revision=_synthetic_handler), runs_root=night.runs).run()
    assert list(outside.iterdir()) == []  # nothing written through the link
    job = _jobs(path)["sambuca-night-revise-codex-openscad"]
    assert job["status"] == "failed" and "symlink" in job["error"]


def test_job_ids_with_matchup_colons_get_a_windows_safe_folder_name(night):
    assert rev.is_slug("sambuca-night-revise-model::level::openscad")
    assert rev.folder_name("a::b+c") == "a--b-c"
