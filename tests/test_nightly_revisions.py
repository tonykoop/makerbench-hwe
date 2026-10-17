"""#1116: the nightly runner consumes Morning Review revision requests (fakes only, no model runs)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from makerbench import nightly_revisions as rev
from makerbench.arena_studio import control_room as cr
from makerbench.nightly_cad import NightlyExecutor
from makerbench.run_log_io import atomic_write_json

from test_nightly_cad import _image, _queue, _registry, _synthetic_handler  # noqa: E402 - sibling helpers

NOW = datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)
ENTRANT = "codex-openscad"


def _handlers(**extra):
    return {"cadam": _synthetic_handler, "arena": _synthetic_handler, "live": _synthetic_handler, **extra}


@pytest.fixture
def night(tmp_path):
    """One finished nightly run (fake handlers) whose run folder revisions point back to."""
    queue = _queue(tmp_path / "queue.json", _image(tmp_path / "reference.png"))
    runs = tmp_path / "runs"

    def executor(**kwargs):
        return NightlyExecutor(
            queue_path=queue, registry_path=_registry(tmp_path / "registry.json"), output_root=runs, now=lambda: NOW, **kwargs
        )

    first = executor(handlers=_handlers()).run()
    return type("Night", (), {"runs": runs, "run_id": first["run_id"], "executor": staticmethod(executor), "tmp": tmp_path})


def _job(night, *, entrant=ENTRANT, feedback="make the soundbox deeper", run_id=None, trial="sambuca__seed0__rep0__codex-openscad", extra=2):
    return {
        "job_id": f"sambuca-night-revise-{entrant}",
        "instrument_id": "sambuca",
        "status": "queued",
        "revision_of": {
            "run_id": run_id or night.run_id, "trial_id": trial, "from_turn": 3, "extra_turns": extra, "feedback": feedback,
        },
        "entrants": [{"entrant_id": entrant}],
    }


def _write_queue(night, *jobs):
    path = night.runs / rev.REVISION_QUEUE
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"schema": rev.REVISION_QUEUE_SCHEMA, "jobs": list(jobs)})
    return path


def _jobs(path):
    return {job["job_id"]: job for job in json.loads(path.read_text())["jobs"]}


class Recorder:
    """Stand-in for the revision generator: writes the trial the way a handler would."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, job, entrant, run_dir, registry):
        self.calls.append((job.revision, entrant.entrant_id, run_dir))
        if self.fail:
            raise RuntimeError(self.fail)
        return _synthetic_handler(job, entrant, run_dir, registry)


def test_schema_constant_matches_the_writer():
    assert rev.REVISION_QUEUE_SCHEMA == cr.REVISION_QUEUE_SCHEMA and rev.REVISION_QUEUE == cr.REVISION_QUEUE


def test_pending_request_becomes_a_nightly_job_and_is_marked_done_with_a_link(night):
    path = _write_queue(night, _job(night))
    recorder = Recorder()
    result = night.executor(handlers=_handlers(revision=recorder), runs_root=night.runs).run()

    assert result["status"] in {"votable", "blocked"}
    (context, entrant_id, run_dir), = recorder.calls
    assert entrant_id == ENTRANT
    assert context["from_turn"] == 3 and context["extra_turns"] == 2
    assert context["feedback"] == "make the soundbox deeper"
    assert context["source_run_dir"] == (night.runs / night.run_id).as_posix()
    assert run_dir != night.runs / night.run_id  # a new run folder, not the source night
    job = _jobs(path)["sambuca-night-revise-codex-openscad"]
    assert job["status"] == "done" and job["new_run"] == {"run_id": result["run_id"]}
    assert result["run_id"] != night.run_id and result["revision_of"]["feedback"] == "make the soundbox deeper"
    assert night.executor(handlers=_handlers(revision=recorder), runs_root=night.runs).run() == {"status": "queue-empty"}


def test_job_inherits_seed_budget_image_and_entrant_config_from_the_source_night(night):
    _write_queue(night, _job(night))
    recorder = Recorder()
    result = night.executor(handlers=_handlers(revision=recorder), runs_root=night.runs).run()
    state = json.loads((Path(result["run_dir"]) / "nightly-state.json").read_text())
    assert state["budget"]["limit_usd"] == 5.0
    run_log = json.loads((Path(result["run_dir"]) / "run_log.json").read_text())
    assert run_log["config"]["model_ids"] == [ENTRANT] and run_log["config"]["seeds"] == [0]


def test_failed_generation_marks_the_request_failed_with_the_reason(night):
    path = _write_queue(night, _job(night))
    night.executor(handlers=_handlers(revision=Recorder(fail="provider exploded")), runs_root=night.runs).run()
    job = _jobs(path)["sambuca-night-revise-codex-openscad"]
    assert job["status"] == "failed" and "provider exploded" in job["error"] and job["new_run"]["run_id"]


def test_without_a_revision_handler_requests_stay_queued(night):
    path = _write_queue(night, _job(night))
    assert night.executor(handlers=_handlers(), runs_root=night.runs).run() == {"status": "queue-empty"}
    assert night.executor(handlers=_handlers(revision=Recorder())).run() == {"status": "queue-empty"}  # no runs_root
    assert _jobs(path)["sambuca-night-revise-codex-openscad"]["status"] == "queued"


def test_latest_save_wins_and_older_pending_duplicates_are_superseded(night):
    old = {**_job(night, feedback="old note"), "job_id": "older"}
    new = {**_job(night, feedback="edited note"), "job_id": "newer"}
    path = _write_queue(night, old, new)
    recorder = Recorder()
    night.executor(handlers=_handlers(revision=recorder), runs_root=night.runs).run()
    assert [call[0]["feedback"] for call in recorder.calls] == ["edited note"]
    jobs = _jobs(path)
    assert jobs["older"]["status"] == "superseded" and jobs["newer"]["status"] == "done"


def test_an_edited_note_saved_through_morning_review_replaces_the_queued_request(night):
    path = night.runs / rev.REVISION_QUEUE
    path.parent.mkdir(parents=True)
    cr.append_to_queue(path, [_job(night, feedback="first draft")], run_id=night.run_id)
    cr.append_to_queue(path, [_job(night, feedback="second thought")], run_id=night.run_id)  # the real writer
    recorder = Recorder()
    night.executor(handlers=_handlers(revision=recorder), runs_root=night.runs).run()
    assert [call[0]["feedback"] for call in recorder.calls] == ["second thought"]


@pytest.mark.parametrize(
    ("job", "reason"),
    [
        (lambda n: _job(n, run_id="20990101T000000Z-not-a-night"), "not in the nightly queue"),
        (lambda n: _job(n, entrant="someone-else"), "did not take part"),
        (lambda n: _job(n, feedback="   "), "malformed"),
        (lambda n: _job(n, extra=0), "malformed"),
        (lambda n: _job(n, run_id="../escape"), "directly under runs"),
    ],
)
def test_unusable_requests_are_failed_with_a_reason_and_do_not_block_the_next(night, job, reason):
    good = {**_job(night, feedback="good", trial="another-design"), "job_id": "good"}
    bad = {**job(night), "job_id": "bad"}
    path = _write_queue(night, bad, good)
    recorder = Recorder()
    night.executor(handlers=_handlers(revision=recorder), runs_root=night.runs).run()
    jobs = _jobs(path)
    assert jobs["bad"]["status"] == "failed" and reason in jobs["bad"]["error"]
    assert jobs["good"]["status"] == "done" and len(recorder.calls) == 1


def test_running_and_finished_requests_are_never_claimed_again(night):
    done = {**_job(night), "job_id": "a", "status": "done"}
    running = {**_job(night), "job_id": "b", "status": "running"}
    _write_queue(night, done, running)
    assert rev.claim_next(night.runs) is None


def test_queue_path_refuses_symlinked_folders_and_files(night, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (night.runs / "control-room").symlink_to(outside, target_is_directory=True)
    with pytest.raises(rev.RevisionQueueError, match="symlink"):
        rev.revision_queue_path(night.runs)
    with pytest.raises(rev.RevisionQueueError):
        rev.claim_next(night.runs)

    (night.runs / "control-room").unlink()
    (night.runs / "control-room").mkdir()
    target = outside / "q.json"
    target.write_text("{}")
    (night.runs / rev.REVISION_QUEUE).symlink_to(target)
    with pytest.raises(rev.RevisionQueueError, match="symlink"):
        rev.revision_queue_path(night.runs)


def test_symlinked_source_run_folder_is_refused(night, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (night.runs / "linked-run").symlink_to(outside, target_is_directory=True)
    with pytest.raises(rev.RevisionQueueError, match="symlink"):
        rev.source_run_dir(night.runs, "linked-run")


def test_wrong_schema_is_rejected_rather_than_consumed(night):
    path = night.runs / rev.REVISION_QUEUE
    path.parent.mkdir(parents=True)
    atomic_write_json(path, {"schema": "something-else", "jobs": [_job(night)]})
    with pytest.raises(rev.RevisionQueueError, match="schema"):
        rev.claim_next(night.runs)


def test_no_queue_file_is_simply_nothing_to_do(night):
    assert rev.claim_next(night.runs) is None and rev.pending_count(night.runs) == 0
    assert not (night.runs / "control-room").exists()  # reading never creates the folder
