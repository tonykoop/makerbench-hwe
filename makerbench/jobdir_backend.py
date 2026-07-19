"""Shared WSL<->Windows job-dir protocol for the SolidWorks/Fusion CAD-backend
axis (#627, follow-up to #601's Blender ``bpy`` backend).

SolidWorks and Fusion 360 scripting only runs inside those apps, which only
run on Windows. The shared filesystem bridge (WSL's ``/mnt/c`` is the same
disk as Windows' ``C:\\``) makes a job-dir handoff the simplest bridge:

1. WSL (this process) writes ``jobs/<trial_id>/{input/,artifacts/,status.json}``
   via :func:`create_job` — the entrant source lands in ``input/`` and
   ``status.json`` starts life as ``"pending"``.
2. A Windows-side watcher script (``scripts/windows/solidworks_job_watcher.ps1``
   / ``fusion_job_watcher.ps1``) polls the same ``jobs/`` directory (visible to
   it as ``C:\\...\\jobs\\``), drives the CAD app, exports STL + a preview PNG
   into ``artifacts/``, and updates ``status.json`` to ``"running"`` then
   ``"done"`` (or ``"error"``).
3. WSL polls the same file via :func:`poll_job`, which blocks (with an
   injectable clock/sleep, so tests never really sleep) until the status
   becomes terminal, then returns the status payload or raises
   ``render.CompileError``.

Nothing downstream of :func:`poll_job` needs to know a Windows process was
involved — the ``solidworks_backend``/``fusion_backend`` adapters turn its
return value into the same ``RenderArtifacts`` shape every other CAD backend
in ``code_cad_arena_runner.BACKEND_COMPILERS`` produces.

``status.json`` schema (``SCHEMA`` below), one JSON object:

| field        | type            | meaning                                                        |
|--------------|-----------------|-----------------------------------------------------------------|
| ``schema``   | str             | ``"makerbench-jobdir-status-v1"``                                |
| ``status``   | str             | one of ``"pending"``, ``"running"``, ``"done"``, ``"error"``     |
| ``trial_id`` | str             | the job's trial id (matches the ``jobs/<trial_id>/`` dir name)   |
| ``backend``  | str \\| null    | ``"solidworks"`` / ``"fusion"`` — which watcher owns this job    |
| ``stl_path`` | str \\| null    | absolute path to the exported STL; set by the watcher on "done"  |
| ``png_path`` | str \\| null    | absolute path to the exported preview PNG; set on "done"         |
| ``units``    | str \\| null    | optional: units the watcher exported in (e.g. ``"mm"``/``"in"``) |
| ``error``    | str \\| null    | human-readable failure detail; set by the watcher on "error"     |

Only ``status`` and ``trial_id`` are required at creation time; the watcher
fills in the rest as the job progresses. Tests must never point
``MAKERBENCH_JOBS_ROOT`` (or the ``root=`` kwarg) at the real repo-root
``jobs/`` directory — always pass a ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from . import render

SCHEMA = "makerbench-jobdir-status-v1"

JOBS_ROOT_ENV = "MAKERBENCH_JOBS_ROOT"
DEFAULT_JOBS_ROOT = Path("jobs")
STATUS_FILENAME = "status.json"

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_POLL_INTERVAL_S = 2.0

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
_TERMINAL_STATUSES = {STATUS_DONE, STATUS_ERROR}
_VALID_STATUSES = {STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR}


def jobs_root() -> Path:
    """Root directory jobs are created under.

    Defaults to ``./jobs`` (relative to the process cwd, matching the
    ``runs/`` convention), overridable via ``MAKERBENCH_JOBS_ROOT`` so tests
    (and real deployments with a different shared-drive layout) never have to
    write into the repo.
    """

    override = os.environ.get(JOBS_ROOT_ENV, "").strip()
    return Path(override) if override else DEFAULT_JOBS_ROOT


def jobdir_handoff_available(*, root: Optional[Path] = None) -> bool:
    """Best-effort preflight: does the WSL<->Windows job-dir handoff path exist?

    This can only check what WSL can actually see: that the Windows
    filesystem bridge (``/mnt/c``) is mounted and that the jobs root exists
    or can be created. It is intentionally NOT a check that SolidWorks or
    Fusion 360 is installed, licensed, or that a watcher script is actually
    running — WSL has no way to introspect a Windows process or a Windows-only
    application from here. A ``True`` return means "the filesystem handoff
    path exists", nothing more; a real run can still stall forever in
    :func:`poll_job` (and time out) if no watcher is listening.
    """

    root_path = root if root is not None else jobs_root()
    if not Path("/mnt/c").is_dir():
        return False
    try:
        root_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def job_dir(trial_id: str, *, root: Optional[Path] = None) -> Path:
    return (root if root is not None else jobs_root()) / trial_id


def status_path(job_dir_path: Path) -> Path:
    return Path(job_dir_path) / STATUS_FILENAME


def write_status(job_dir_path: Path, **fields: object) -> None:
    """Write (overwrite) ``status.json`` for one job.

    Used both by :func:`create_job` (initial ``"pending"`` write) and by
    tests simulating the Windows-side watcher (real deployments write this
    file from PowerShell, not this function — see ``scripts/windows/``).
    """

    payload: dict = {
        "schema": SCHEMA,
        "status": None,
        "trial_id": None,
        "backend": None,
        "stl_path": None,
        "png_path": None,
        "units": None,
        "error": None,
    }
    payload.update(fields)
    if payload["status"] not in _VALID_STATUSES:
        raise ValueError(f"invalid job status {payload['status']!r}; choose one of {sorted(_VALID_STATUSES)}")
    job_dir_path = Path(job_dir_path)
    job_dir_path.mkdir(parents=True, exist_ok=True)
    status_path(job_dir_path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_status(job_dir_path: Path) -> Optional[dict]:
    """Read ``status.json``, or ``None`` if it doesn't exist (yet)."""

    path = status_path(job_dir_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A watcher mid-write can leave a transient partial/unreadable file;
        # treat it like "not there yet" rather than crashing the poller.
        return None


def create_job(
    trial_id: str,
    source_path: Path,
    *,
    backend: str,
    root: Optional[Path] = None,
    source_filename: Optional[str] = None,
) -> Path:
    """Create ``jobs/<trial_id>/{input/,artifacts/,status.json}`` and return the job dir.

    The entrant source is copied into ``input/`` (as text; SolidWorks VBA
    macros and Fusion API scripts are both plain text) and ``status.json``
    starts as ``"pending"``.
    """

    jdir = job_dir(trial_id, root=root)
    (jdir / "input").mkdir(parents=True, exist_ok=True)
    (jdir / "artifacts").mkdir(parents=True, exist_ok=True)
    dest_name = source_filename or Path(source_path).name or "entrant.txt"
    dest = jdir / "input" / dest_name
    dest.write_text(Path(source_path).read_text(encoding="utf-8"), encoding="utf-8")
    write_status(
        jdir,
        status=STATUS_PENDING,
        trial_id=trial_id,
        backend=backend,
        stl_path=None,
        png_path=None,
        units=None,
        error=None,
    )
    return jdir


def poll_job(
    job_dir_path: Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> dict:
    """Block until the job's ``status.json`` reaches a terminal state.

    Returns the status payload on ``"done"``. Raises ``render.CompileError``
    on ``"error"`` (using the status file's ``error`` message) or on timeout
    (a clear "Windows-side runner did not complete within Ns" message) — the
    same exception type every other backend compiler raises for a
    candidate-caused (or environment-caused, here) failure, so
    ``evaluate_objective_trial`` records the same ``auto_fail`` shape.

    ``sleep_fn``/``clock_fn`` mirror ``code_cad_orchestrator``'s injectable
    clock pattern: tests pass a fake clock that advances past ``timeout_s``
    without ever sleeping for real.
    """

    start = clock_fn()
    while True:
        payload = read_status(job_dir_path)
        status = (payload or {}).get("status") if payload else None

        if status == STATUS_DONE:
            stl_path = (payload or {}).get("stl_path")
            if not stl_path or not Path(stl_path).exists():
                raise render.CompileError(
                    f"Windows-side runner reported job {job_dir_path} 'done' but "
                    f"stl_path {stl_path!r} does not exist"
                )
            return payload

        if status == STATUS_ERROR:
            detail = (payload or {}).get("error") or "unknown error"
            raise render.CompileError(
                f"Windows-side runner reported an error for job {job_dir_path}: {detail}"
            )

        elapsed = clock_fn() - start
        if elapsed >= timeout_s:
            raise render.CompileError(
                f"Windows-side runner did not complete within {timeout_s}s "
                f"(job {job_dir_path}, last status={status!r})"
            )

        sleep_fn(poll_interval_s)
