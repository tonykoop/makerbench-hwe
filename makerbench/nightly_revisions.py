"""Nightly side of Control Room revision requests (#1116, part of #1104).

Morning Review (``arena_studio/control_room.py``) appends revision jobs to
``runs/control-room/revision-queue.json``. This module is the consumer the nightly runner
uses: it claims one pending request at a time, turns it into a ``NightlyJob`` that continues the
named entrant from its best turn with Tony's note as feedback, and records the outcome
(``done`` / ``failed``) with a link to the new run.

Queue rules, matching the writer:

- only ``queued`` jobs are claimed; claiming happens under the queue's file lock, so a Morning
  Review save (which replaces still-``queued`` jobs under the same lock) never races a claim;
- several queued requests for the same design resolve to the latest one; the others become
  ``superseded`` so an edited note is never run twice;
- the queue path is resolved without creating anything and refused when ``runs/``, the
  ``control-room`` folder or the file is a symlink, or the folder resolves outside ``runs/``;
- the source night's run folder is confined the same way.

Nothing here talks to a model: generation is the ``revision`` handler the executor is given.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .run_log_io import atomic_write_json, file_lock

REVISION_QUEUE_SCHEMA = "makerbench-control-room-revision-queue-v1"  # == control_room.REVISION_QUEUE_SCHEMA
REVISION_QUEUE = Path("control-room") / "revision-queue.json"

PENDING = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SUPERSEDED = "superseded"


# Ids that name folders or jobs: no separators, no spaces, no leading dot, no ``..``. ``:`` and ``+``
# are allowed in a job id (matchup entrant ids use ``::``) but never reach a folder name raw.
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,119}")


def is_slug(value: object) -> bool:
    return isinstance(value, str) and bool(_SLUG.fullmatch(value)) and ".." not in value


def folder_name(value: str) -> str:
    """A job id as a folder-name component (``:`` and ``+`` become ``-``; safe on Windows too)."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", value)


class RevisionQueueError(ValueError):
    """The revision queue (or a run folder it names) cannot be used where it is."""


@dataclass(frozen=True)
class RevisionRequest:
    job_id: str
    instrument_id: str
    entrant_id: str
    source_run_id: str
    trial_id: str
    from_turn: Any  # a turn number, or "best" when the review recorded none
    extra_turns: int
    feedback: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_run_id": self.source_run_id,
            "trial_id": self.trial_id,
            "entrant_id": self.entrant_id,
            "from_turn": self.from_turn,
            "extra_turns": self.extra_turns,
            "feedback": self.feedback,
        }


def confined_child(root: Path, *parts: str) -> Path:
    """``root/parts`` without creating anything; refuses symlinks and escapes from ``root``."""
    base = Path(root)
    path = base
    for part in (None, *parts):
        if part is not None:
            path = path / part
        if path.is_symlink():
            raise RevisionQueueError(f"Refusing to use {path.name} through a symlink.")
    if not path.resolve().is_relative_to(base.resolve()):
        raise RevisionQueueError(f"{path.name} resolves outside {base.name}/.")
    return path


def revision_queue_path(runs_root: Path) -> Path:
    return confined_child(runs_root, *REVISION_QUEUE.parts)


def source_run_dir(runs_root: Path, run_id: str) -> Path:
    if not is_slug(run_id):
        raise RevisionQueueError("A revision must name a run folder directly under runs/.")
    return confined_child(runs_root, run_id)


def _read(queue_path: Path) -> dict[str, Any]:
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != REVISION_QUEUE_SCHEMA:
        raise RevisionQueueError(f"revision queue schema must be {REVISION_QUEUE_SCHEMA}")
    if not isinstance(payload.get("jobs", []), list):
        raise RevisionQueueError("revision queue jobs must be a list")
    return payload


def _request(job: Mapping[str, Any]) -> Optional[RevisionRequest]:
    of = job.get("revision_of")
    entrants = job.get("entrants")
    if not isinstance(of, Mapping) or not isinstance(entrants, list) or not entrants:
        return None
    entrant = entrants[0].get("entrant_id") if isinstance(entrants[0], Mapping) else None
    feedback = of.get("feedback")
    extra = of.get("extra_turns")
    if not (job.get("job_id") and job.get("instrument_id") and entrant and of.get("run_id") and of.get("trial_id")):
        return None
    if not is_slug(job.get("job_id")) or not is_slug(of.get("run_id")):
        return None  # these become folder names: refuse anything that is not a plain slug
    if not isinstance(feedback, str) or not feedback.strip():
        return None
    if not isinstance(extra, int) or isinstance(extra, bool) or extra < 1:
        return None
    return RevisionRequest(
        job_id=str(job["job_id"]),
        instrument_id=str(job["instrument_id"]),
        entrant_id=str(entrant),
        source_run_id=str(of["run_id"]),
        trial_id=str(of["trial_id"]),
        from_turn=of.get("from_turn", "best"),
        extra_turns=extra,
        feedback=feedback.strip(),
    )


def _stamp(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def pending_count(runs_root: Path) -> int:
    """Queued requests, for reporting; 0 when there is no queue yet."""
    path = revision_queue_path(runs_root)
    if not path.is_file():
        return 0
    return sum(1 for job in _read(path).get("jobs", []) if isinstance(job, Mapping) and job.get("status") == PENDING)


def parent_run(runs_root: Path, run_id: str) -> Optional[str]:
    """The run a revision run continued, from the ledger (``new_run`` link + ``revision_of``).

    A revision run never enters the nightly queue, so this is how a later request to revise a
    revised design finds where that design's entrant configuration came from.
    """
    path = revision_queue_path(runs_root)
    if not path.is_file():
        return None
    for job in _read(path).get("jobs", []):
        if isinstance(job, Mapping) and isinstance(job.get("new_run"), Mapping) and job["new_run"].get("run_id") == run_id:
            parent = job.get("revision_of")
            if isinstance(parent, Mapping) and is_slug(parent.get("run_id")):
                return str(parent["run_id"])
    return None


MAX_CHAIN = 50


def claim_next(runs_root: Path, *, now: Optional[datetime] = None) -> Optional[RevisionRequest]:
    """Mark the next pending request ``running`` and return it; ``None`` when nothing is pending.

    Malformed pending jobs are marked ``failed`` (with a reason) rather than blocking the queue,
    and an older pending request for the same design as a later one is marked ``superseded``.
    """
    path = revision_queue_path(runs_root)
    if not path.is_file():
        return None
    with file_lock(path):
        payload = _read(path)
        jobs = payload.get("jobs", [])
        latest: dict[tuple[str, str], int] = {}
        for index, job in enumerate(jobs):
            if isinstance(job, dict) and job.get("status") == PENDING:
                of = job.get("revision_of") if isinstance(job.get("revision_of"), Mapping) else {}
                latest[(str(of.get("run_id")), str(of.get("trial_id")))] = index
        claimed: Optional[RevisionRequest] = None
        for index, job in enumerate(jobs):
            if not isinstance(job, dict) or job.get("status") != PENDING:
                continue
            of = job.get("revision_of") if isinstance(job.get("revision_of"), Mapping) else {}
            if latest.get((str(of.get("run_id")), str(of.get("trial_id")))) != index:
                job.update(status=SUPERSEDED, finished_utc=_stamp(now), note="a later save for this design won")
                continue
            request = _request(job)
            if request is None:
                job.update(status=FAILED, finished_utc=_stamp(now), error="malformed revision request")
                continue
            if claimed is None:
                job.update(status=RUNNING, claimed_utc=_stamp(now))
                claimed = request
        atomic_write_json(path, payload)
    return claimed


def finish(
    runs_root: Path,
    request: RevisionRequest,
    *,
    ok: bool,
    run_id: Optional[str] = None,
    error: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Record the outcome on the request: ``done`` / ``failed`` plus a link to the new run."""
    path = revision_queue_path(runs_root)
    with file_lock(path):
        payload = _read(path)
        for job in payload.get("jobs", []):
            if isinstance(job, dict) and job.get("job_id") == request.job_id and job.get("status") == RUNNING:
                job.update(status=DONE if ok else FAILED, finished_utc=_stamp(now))
                if run_id:
                    job["new_run"] = {"run_id": run_id}
                if error:
                    job["error"] = error[:500]
                break
        else:
            raise RevisionQueueError(f"revision {request.job_id} is no longer running in the queue")
        atomic_write_json(path, payload)
