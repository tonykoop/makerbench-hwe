"""Shared, concurrency-safe I/O helpers for arena `run_log.json` files (#619).

Two independent writers can touch the same run log at once: the `arena run`
orchestrator (`code_cad_orchestrator.py`), which holds the full log in memory
and rewrites it after every completed trial, and `arena ingest-candidate`
(`code_cad_arena_runner.py`), which does a one-off read-modify-write to append
an externally-generated candidate row. Without coordination this is a classic
last-writer-wins race: whichever writer's `write_text` lands last clobbers the
other's update, and a resume onto a run dir with a different model matrix can
drop whole rows outright.

This module gives both call sites the same three primitives so the write path
is identical everywhere:

- `file_lock`: an `fcntl.flock`-based exclusive lock on a sidecar
  `<name>.lock` file, taken for the whole read-modify-write span.
- `atomic_write_json`: write-to-temp-then-`os.replace` so a reader never
  observes a half-written file.
- `merge_trial_rows`: union on-disk trial rows with the caller's "managed"
  rows by `trial_id` - managed rows win for the ids the caller is
  authoritative over, everything else on disk passes through untouched.

POSIX-only (`fcntl.flock`) - this repo runs on Linux/WSL, not Windows.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from .track_isolation import is_blender_row


EXTERNAL_STEP = "external_step"


def _off_track(entry: Mapping[str, object], config: Mapping[str, object]) -> bool:
    """Not a blind code-CAD trial: external STEP export, or a Blender (mesh) trial."""
    return is_external_step(entry) or is_blender_row(entry, config)


def is_external_step(entry: Mapping[str, object]) -> bool:
    """External exports never enter blind code-CAD scorelines, reports or vote queues."""
    return any(
        isinstance(part, Mapping) and part.get(key) == EXTERNAL_STEP
        for part in (entry, entry.get("result"), entry.get("meta"))
        for key in ("entrant_type", "modality", "track")
    )


def trial_rows(run_log: Mapping[str, object]) -> list:
    """The run log's blind code-CAD trial rows: the ONE reader for scoring and publishing.

    Rows that are not on the code-CAD tracks are excluded here, before any cell
    indexing, summing or ranking: external (STEP) exports (#1069) and Blender
    (mesh, freeform) trials (#1041), whether tagged on the row, its result/meta, or
    the whole run's config. Every consumer that aggregates, ranks, reports, queues
    or publishes trials must iterate this, never ``run_log["trials"]``;
    ``tests/test_track_isolation_publishers.py`` fails on any other raw access.
    Writers that must preserve every row (orchestrator merge, ingest) and by
    ``trial_id`` lookups keep the raw list (explicit allowlist in that test).
    """
    config = run_log.get("config") or {}
    if is_external_step(config) or is_blender_row({}, config):
        return []
    return [row for row in run_log.get("trials") or [] if not _off_track(row, config)]


def find_trial(
    run_log: Mapping[str, object], trial_id: str, *, unique: bool = False
) -> Mapping[str, object] | None:
    """The row with ``trial_id`` among EVERY stored row (a by-id lookup, not an aggregate).

    Deliberately unfiltered: callers name a specific row (a vote reveal, a resume check) and
    must find it whatever track it is on. Never iterate rows through this. With ``unique``,
    a duplicated id (an ambiguous log) returns None instead of the first match.
    """
    matches = [row for row in run_log.get("trials") or [] if row.get("trial_id") == trial_id]
    if unique:
        return matches[0] if len(matches) == 1 else None
    return matches[0] if matches else None


def has_trial(run_log: Mapping[str, object], trial_id: str) -> bool:
    """Whether any stored row carries ``trial_id`` (resume/dup checks in writers)."""
    return find_trial(run_log, trial_id) is not None


def trials_by_id(run_log: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """``{trial_id: row}`` over every stored row, for resolving ids named elsewhere (votes)."""
    return {str(row.get("trial_id")): row for row in run_log.get("trials") or []}


def summary_trials(summary: Mapping[str, object]) -> list:
    """Trial rows of a Studio run summary, which is itself built from :func:`trial_rows`."""
    return list(summary.get("trials") or [])


def lock_path_for(path: Path) -> Path:
    """Return the sidecar lock path for a run log (or any JSON file)."""

    return path.parent / f"{path.name}.lock"


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive POSIX lock for the duration of the `with` block.

    Callers should do their read-modify-write of `path` entirely inside this
    block, re-reading the file *after* acquiring the lock so they see the
    latest committed state rather than whatever they read before blocking.
    """

    lock_file = lock_path_for(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write `payload` as JSON via temp-file-then-`os.replace`.

    Readers (including a concurrent lock-holder that reads before this call
    commits) never observe a partially-written file - `os.replace` is atomic
    on POSIX within the same filesystem.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def merge_trial_rows(
    disk_trials: Sequence[Mapping[str, object]],
    managed: Mapping[str, Mapping[str, object]],
) -> list[dict]:
    """Union on-disk trial rows with the caller's authoritative rows.

    `managed` rows win for the `trial_id`s they cover (the caller has the
    freshest status for those ids). Any `trial_id` present on disk but absent
    from `managed` - an externally-ingested row, or a row from a run dir's
    prior, differently-shaped matrix - passes through unchanged rather than
    being dropped. This is the fix for both #619 races: the ingest-vs-run
    last-writer-wins loss, and the resume-with-different-matrix wipe.
    """

    by_id: dict[str, dict] = {}
    order: list[str] = []
    for row in disk_trials:
        trial_id = row.get("trial_id")
        if trial_id not in by_id:
            order.append(trial_id)
        by_id[trial_id] = dict(row)
    for trial_id, row in managed.items():
        if trial_id not in by_id:
            order.append(trial_id)
        by_id[trial_id] = dict(row)
    return [by_id[trial_id] for trial_id in order]
