"""Design workbench service and job runner (#788 W3).

Glue between the append-only :mod:`makerbench.workbench_store` (W2), the
sandboxed compilers (W0 ``scad_sandbox``; the CadQuery backend's own
Bubblewrap) and the parameter rewriter (W1 ``cad_params``), exposed through
``routes_workbench.py``.

**Jobs are drafts.** A compile (from an edit or a parameter apply) is one
draft with one job; the draft id is the job id. Each job runs as a detached
``python -m makerbench.cli arena workbench-job --draft-dir <dir>`` process
whose stdout/stderr is ``job.log`` in the draft directory, the same shape as
the Studio's ``launch_competition``. Status derives from the process handle
(or, after a Studio restart, from the pid) plus the ``job`` block the child
writes into ``draft.json``; a dead pid with an unfinished status becomes
``interrupted``. Limits: one running job per design, two per server, a queue
of eight; beyond that the API answers 429.

Nothing here compiles on the host. ``run_job`` uses
``compiler_for_backend(backend, sandboxed=True)``; an unavailable sandbox is a
``failed`` draft whose error names ``sandbox_unavailable``, never a fallback.
Model revisions (``revise``) are W6 and are refused here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from makerbench import render
from makerbench.cad_params import ParameterError, apply_parameters, changed_values, extract_parameters
from makerbench.code_cad_arena_runner import compiler_for_backend, load_arena_registry, mesh_objective_gate
from makerbench.code_cad_objective import ObjectiveContext, _artifact_payload, _normalize_gate_result
from makerbench.entrant_sandbox import SandboxUnavailable
from makerbench.workbench_store import (
    BACKENDS,
    FINISHED_STATUSES,
    Conflict,
    NotFound,
    WorkbenchError,
    WorkbenchStore,
    is_valid_id,
)

__all__ = [
    "MAX_RUNNING_PER_DESIGN",
    "MAX_RUNNING_PER_SERVER",
    "MAX_QUEUED",
    "MAX_PARAMS",
    "JOB_LOG_CAP_BYTES",
    "QueueFull",
    "WorkbenchService",
    "run_job",
]

MAX_RUNNING_PER_DESIGN = 1
MAX_RUNNING_PER_SERVER = 2
MAX_QUEUED = 8
#: Parameter values one apply may carry (G7).
MAX_PARAMS = 500
#: Export target inside an instrument repo (W7, G14): never ``cad/``.
EXPORT_SUBDIR = "arena/workbench"
EXPORT_SCHEMA = "makerbench-workbench-export-v1"
#: ``job.log`` is capped: the tail served to the browser never exceeds this.
JOB_LOG_CAP_BYTES = 2 * 1024 * 1024
#: Longest compile stderr the job copies into the log.
_STDERR_LOG_CAP = 64 * 1024

BLANK_SOURCES = {
    "openscad": "// New design. Top-level `name = value;` lines become parameters.\nsize_mm = 20;\ncube(size_mm);\n",
    "cadquery": "\"\"\"New design. Module-level NAME = literal lines become parameters.\"\"\"\nimport cadquery as cq\n\nSIZE_MM = 20.0\n\nresult = cq.Workplane(\"XY\").box(SIZE_MM, SIZE_MM, SIZE_MM)\n",
}
SOURCE_SUFFIXES = {"openscad": ".scad", "cadquery": ".py"}


class QueueFull(WorkbenchError):
    """The compile queue is full; the API maps it to 429."""


_TRIAL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pid_is_workbench_job(pid: object, draft_dir: Path) -> bool:
    """Alive *and* the workbench job for this draft (never a recycled pid)."""

    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    cmdline_path = Path("/proc") / str(pid) / "cmdline"
    if cmdline_path.exists():
        try:
            cmdline = cmdline_path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
        except OSError:
            return False
        return "workbench-job" in cmdline and str(draft_dir) in cmdline
    return True


class WorkbenchService:
    """Designs, drafts, jobs and revisions for the Studio."""

    def __init__(
        self,
        *,
        repo_root: Path,
        registry_path: Path,
        instruments_root: Optional[Path] = None,
        source_root: Optional[Path] = None,
        max_running_per_design: int = MAX_RUNNING_PER_DESIGN,
        max_running_per_server: int = MAX_RUNNING_PER_SERVER,
        max_queued: int = MAX_QUEUED,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.registry_path = Path(registry_path).resolve()
        self.instruments_root = Path(instruments_root).resolve() if instruments_root else None
        self.source_root = Path(source_root).resolve() if source_root else Path(__file__).resolve().parents[2]
        self.store = WorkbenchStore(self.repo_root / "runs" / "workbench")
        self.max_running_per_design = max_running_per_design
        self.max_running_per_server = max_running_per_server
        self.max_queued = max_queued
        self._processes: dict[tuple[str, str], subprocess.Popen] = {}
        self._queue: list[tuple[str, str]] = []
        self._registry: Optional[dict] = None
        self._rediscover()

    # --- registry ---------------------------------------------------------

    def _registry_payload(self) -> dict:
        if self._registry is None:
            try:
                self._registry = load_arena_registry(self.registry_path)
            except (OSError, ValueError):
                self._registry = {"instruments": []}
        return self._registry

    def _spec(self, instrument_id: str) -> Optional[dict]:
        for spec in self._registry_payload().get("instruments", []):
            if spec.get("id") == instrument_id:
                return dict(spec)
        return None

    # --- origins ----------------------------------------------------------

    def _repo_dir(self, instrument_id: str, *, what: str = "masters") -> tuple[Path, str]:
        """``<instruments_root>/<repo_path>`` for a registry instrument, contained
        and never through ``private`` or a symlink: (resolved dir, repo_path)."""

        if self.instruments_root is None:
            raise WorkbenchError(f"the Studio was started without --instruments-root; {what} are unavailable")
        if not is_valid_id(instrument_id):
            raise NotFound("unknown instrument")
        spec = self._spec(instrument_id)
        if spec is None:
            raise NotFound("unknown instrument")
        repo_path = str(spec.get("repo_path") or "")
        if not repo_path:
            raise WorkbenchError(f"instrument {instrument_id!r} has no repo_path in the registry")
        parts = Path(repo_path).parts
        if not parts or any(p == ".." or p.lower() == "private" for p in parts) or Path(repo_path).is_absolute():
            raise NotFound("unknown instrument")
        # Walk the *unresolved* path one component at a time: a symlink
        # anywhere in it (not only at the cad/ entry) could point into a
        # private tree or outside the root, so every component must be a
        # real directory.
        current = self.instruments_root
        for part in parts:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise NotFound("unknown instrument")
        repo_dir = current.resolve()
        if not repo_dir.is_relative_to(self.instruments_root) or not repo_dir.is_dir():
            raise NotFound("instrument repo is not under the instruments root")
        if any(p.lower() == "private" for p in repo_dir.relative_to(self.instruments_root).parts):
            raise NotFound("unknown instrument")
        return repo_dir, Path(*parts).as_posix()

    def _master_cad_dir(self, instrument_id: str) -> Path:
        """``<instruments_root>/<repo_path>/cad`` (any case) for a registry
        instrument, contained and never through ``private``."""

        repo_dir, _repo_path = self._repo_dir(instrument_id)
        for entry in os.scandir(repo_dir):
            if entry.name.lower() == "cad" and entry.is_dir() and not entry.is_symlink():
                cad_dir = Path(entry.path).resolve()
                if cad_dir.is_relative_to(self.instruments_root) and not any(
                    p.lower() == "private" for p in cad_dir.relative_to(self.instruments_root).parts
                ):
                    return cad_dir
        raise NotFound("instrument has no cad/ directory")

    def master_files(self, instrument_id: str) -> list[str]:
        cad_dir = self._master_cad_dir(instrument_id)
        names = []
        for entry in sorted(os.scandir(cad_dir), key=lambda e: e.name):
            if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in (".scad", ".py"):
                names.append(entry.name)
        return names

    def _master_source(self, instrument_id: str, file: str) -> tuple[str, str, str]:
        if not isinstance(file, str) or not file or "/" in file or "\\" in file or file.startswith(".") or "private" in file.lower():
            raise NotFound("unknown master file")
        suffix = Path(file).suffix.lower()
        backend = {".scad": "openscad", ".py": "cadquery"}.get(suffix)
        if backend is None:
            raise WorkbenchError("master file must end in .scad or .py")
        cad_dir = self._master_cad_dir(instrument_id)
        candidate = cad_dir / file
        if candidate.is_symlink() or not candidate.is_file():
            raise NotFound("unknown master file")
        resolved = candidate.resolve()
        if resolved.parent != cad_dir:
            raise NotFound("unknown master file")
        rel = resolved.relative_to(self.instruments_root).as_posix()  # type: ignore[arg-type]
        return resolved.read_text(encoding="utf-8", errors="replace"), backend, rel

    def _trial_source(self, run_dir: Path, run_id: str, trial_id: str) -> tuple[str, str, dict]:
        run_dir = Path(run_dir).resolve()
        if not isinstance(trial_id, str) or not _TRIAL_ID_RE.fullmatch(trial_id):
            raise NotFound("unknown trial")
        try:
            log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise NotFound("run has no readable run_log.json") from None
        trial = next((t for t in log.get("trials", []) if t.get("trial_id") == trial_id), None)
        if trial is None:
            raise NotFound("unknown trial")
        result = trial.get("result") or {}
        raw = (result.get("gen") or {}).get("scad_path") or result.get("scad_path")
        if not raw:
            raise WorkbenchError("trial has no generated source to open")
        source_path = Path(str(raw))
        if not source_path.is_absolute():
            source_path = run_dir / source_path
        source_path = source_path.resolve()
        if not source_path.is_relative_to(run_dir) or not source_path.is_file():
            raise NotFound("trial source is not inside its run directory")
        backend = str((log.get("config") or {}).get("backend") or "openscad")
        if backend not in BACKENDS:
            raise WorkbenchError(f"run backend {backend!r} has no workbench support")
        origin = {
            "kind": "trial",
            "run_id": run_id,
            "trial_id": trial_id,
            "model_id": str(trial.get("model_id") or ""),
            "instrument_id": str(trial.get("instrument_id") or ""),
        }
        return source_path.read_text(encoding="utf-8", errors="replace"), backend, origin

    def create_design(
        self,
        *,
        origin: Mapping[str, Any],
        title: str = "",
        voter: str = "tony",
        run_dir: Optional[Path] = None,
    ) -> dict:
        """Create a design from a trial, a master or a blank, then enqueue the
        origin compile. Returns ``{"design": ..., "draft": ...}``."""

        if not isinstance(origin, Mapping):
            raise WorkbenchError("origin must be an object")
        if "trial" in origin:
            spec = origin["trial"] or {}
            run_id = str(spec.get("run_id") or "")
            trial_id = str(spec.get("trial_id") or "")
            if run_dir is None:
                raise NotFound("unknown run")
            source, backend, origin_row = self._trial_source(run_dir, run_id, trial_id)
            instrument_id = origin_row["instrument_id"]
        elif "master" in origin:
            spec = origin["master"] or {}
            instrument_id = str(spec.get("instrument_id") or "")
            source, backend, rel = self._master_source(instrument_id, str(spec.get("file") or ""))
            origin_row = {"kind": "master", "instrument_id": instrument_id, "repo_rel_path": rel, "file": str(spec.get("file"))}
        elif "blank" in origin:
            spec = origin["blank"] or {}
            backend = str(spec.get("backend") or "openscad")
            if backend not in BACKENDS:
                raise WorkbenchError(f"backend must be one of {BACKENDS}")
            instrument_id = str(spec.get("instrument_id") or "blank")
            source = BLANK_SOURCES[backend]
            origin_row = {"kind": "blank", "instrument_id": instrument_id}
        else:
            raise WorkbenchError("origin must contain one of trial, master or blank")
        if not is_valid_id(instrument_id):
            raise WorkbenchError("instrument_id must match [a-z0-9][a-z0-9_-]{0,63}")
        self._check_capacity()
        design = self.store.create_design(instrument_id=instrument_id, backend=backend, title=title, origin=origin_row)
        draft = self.store.create_draft(
            design["design_id"], parent_rev_id=None, source=source,
            editor={"kind": "human", "voter": voter}, kind="edit",
        )
        self._enqueue(design["design_id"], draft["draft_id"])
        return {"design": design, "draft": self.refresh_draft(design["design_id"], draft["draft_id"])}

    # --- drafts -----------------------------------------------------------

    def _parent_source(self, design_id: str, parent_rev_id: Optional[str]) -> str:
        if parent_rev_id is None:
            raise Conflict("name the parent_rev_id to edit")
        return self.store.revision_source(design_id, parent_rev_id)

    def start_edit(self, design_id: str, *, parent_rev_id: Optional[str], source: str, voter: str = "tony") -> dict:
        self.store.read_design(design_id)
        self._check_capacity()
        draft = self.store.create_draft(
            design_id, parent_rev_id=parent_rev_id, source=source,
            editor={"kind": "human", "voter": voter}, kind="edit",
        )
        self._enqueue(design_id, draft["draft_id"])
        return self.refresh_draft(design_id, draft["draft_id"])

    def start_params(self, design_id: str, *, parent_rev_id: Optional[str], values: Mapping[str, Any], voter: str = "tony") -> dict:
        if not isinstance(values, Mapping) or not values:
            raise WorkbenchError("params must be a non-empty object")
        if len(values) > MAX_PARAMS:
            raise WorkbenchError(f"at most {MAX_PARAMS} parameters per apply")
        design = self.store.read_design(design_id)
        backend = design["backend"]
        base = self._parent_source(design_id, parent_rev_id)
        try:
            before = extract_parameters(base, backend)
            source = apply_parameters(base, dict(values), backend)
            after = extract_parameters(source, backend)
        except ParameterError as exc:
            raise WorkbenchError(str(exc)) from exc
        changed = changed_values(before, after)
        if not changed:
            raise Conflict("no parameter changed; nothing to compile")
        self._check_capacity()
        draft = self.store.create_draft(
            design_id, parent_rev_id=parent_rev_id, source=source,
            editor={"kind": "parameters", "changed": changed, "voter": voter}, kind="parameters",
        )
        self._enqueue(design_id, draft["draft_id"])
        return self.refresh_draft(design_id, draft["draft_id"])

    def parameters(self, design_id: str, rev_id: str) -> dict:
        design = self.store.read_design(design_id)
        source = self.store.revision_source(design_id, rev_id)
        payload = extract_parameters(source, design["backend"]).to_dict()
        # W5: the registry envelope is context next to size-like parameters,
        # never a slider bound (plan Q7). Absent when the instrument is unknown.
        spec = self._spec(design.get("instrument_id", ""))
        envelope = spec.get("envelope_mm") if spec else None
        payload["envelope_mm"] = list(envelope) if isinstance(envelope, (list, tuple)) and len(envelope) == 3 else None
        return payload

    # --- jobs -------------------------------------------------------------

    def _rediscover(self) -> None:
        """After a Studio restart: running drafts whose pid is gone become
        ``interrupted``; queued drafts re-enter the queue."""

        for design in self.store.list_designs():
            design_id = design["design_id"]
            for draft in self.store.list_drafts(design_id):
                job = draft.get("job") or {}
                key = (design_id, draft["draft_id"])
                if job.get("status") == "queued" and key not in self._queue:
                    self._queue.append(key)
                elif job.get("status") == "running":
                    draft_dir = self.store.draft_dir(design_id, draft["draft_id"])
                    if not _pid_is_workbench_job(job.get("pid"), draft_dir):
                        self.store.update_draft_job(
                            design_id, draft["draft_id"], status="interrupted", finished_at=_now(),
                            error="the Studio restarted while this job ran; compile again",
                        )
        self._queue.sort()
        self._pump()

    def _check_capacity(self) -> None:
        """Called before a draft is created, so a full queue leaves no orphan."""

        self._pump()
        if len(self._queue) >= self.max_queued:
            raise QueueFull(f"compile queue full ({self.max_queued} queued); try again shortly")

    def _enqueue(self, design_id: str, draft_id: str) -> None:
        self._queue.append((design_id, draft_id))
        self._pump()

    def _running(self) -> list[tuple[str, str]]:
        running = []
        for design in self.store.list_designs():
            for draft in self.store.list_drafts(design["design_id"]):
                if (draft.get("job") or {}).get("status") == "running":
                    key = (design["design_id"], draft["draft_id"])
                    if self.refresh_draft(*key)["job"]["status"] == "running":
                        running.append(key)
        return running

    def _pump(self) -> None:
        running = self._running()
        for key in list(self._queue):
            if len(running) >= self.max_running_per_server:
                break
            design_id, draft_id = key
            if sum(1 for d, _ in running if d == design_id) >= self.max_running_per_design:
                continue
            self._queue.remove(key)
            try:
                self._launch(design_id, draft_id)
            except (NotFound, OSError) as exc:
                try:
                    self.store.update_draft_job(design_id, draft_id, status="failed", finished_at=_now(), error=f"could not start: {exc}")
                except NotFound:
                    pass
                continue
            running.append(key)

    def _launch(self, design_id: str, draft_id: str) -> None:
        draft_dir = self.store.draft_dir(design_id, draft_id)
        log_path = draft_dir / "job.log"
        command = [
            sys.executable, "-m", "makerbench.cli", "arena", "workbench-job",
            "--draft-dir", str(draft_dir), "--registry", str(self.registry_path),
        ]
        with log_path.open("ab") as handle:
            handle.write(f"=== WORKBENCH JOB START {draft_id} ===\n".encode("utf-8"))
            handle.flush()
            popen_kwargs: dict[str, Any] = {
                "cwd": str(self.source_root),
                "stdin": subprocess.DEVNULL,
                "stdout": handle,
                "stderr": subprocess.STDOUT,
                "close_fds": True,
            }
            if os.name == "nt":  # pragma: no cover - Windows
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            else:
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **popen_kwargs)
        self._processes[(design_id, draft_id)] = process
        self.store.update_draft_job(design_id, draft_id, status="running", pid=process.pid, started_at=_now())

    def refresh_draft(self, design_id: str, draft_id: str) -> dict:
        """The draft with its job status derived from the live process."""

        draft = self.store.read_draft(design_id, draft_id)
        job = draft["job"]
        key = (design_id, draft_id)
        process = self._processes.get(key)
        status = job.get("status")
        updates: dict[str, Any] = {}
        if status in FINISHED_STATUSES:
            self._processes.pop(key, None)
        elif process is not None:
            rc = process.poll()
            if rc is not None:
                # The child writes its own final status; a child that died
                # without doing so was interrupted (killed, OOM, crash).
                latest = self.store.read_draft(design_id, draft_id)["job"]
                if latest.get("status") in FINISHED_STATUSES:
                    job = latest
                else:
                    updates = {"status": "interrupted", "exit_code": rc, "finished_at": _now(),
                               "error": f"job process exited {rc} without a result"}
                self._processes.pop(key, None)
        elif status == "running":
            draft_dir = self.store.draft_dir(design_id, draft_id)
            if not _pid_is_workbench_job(job.get("pid"), draft_dir):
                updates = {"status": "interrupted", "finished_at": _now(),
                           "error": "job process is gone without a result"}
        if updates:
            # Re-read before writing: a cancel (or the job's own final write)
            # may have landed since this refresh read the job block, and a
            # finished status is never overwritten with ``interrupted``.
            latest = self.store.read_draft(design_id, draft_id)["job"]
            if latest.get("status") in FINISHED_STATUSES:
                job = latest
                updates = {}
        if updates:
            self.store.update_draft_job(design_id, draft_id, **updates)
            draft = self.store.read_draft(design_id, draft_id)
        else:
            draft["job"] = job
        draft["queue_position"] = self._queue.index(key) + 1 if key in self._queue else None
        return draft

    def cancel(self, design_id: str, draft_id: str) -> dict:
        draft = self.refresh_draft(design_id, draft_id)
        key = (design_id, draft_id)
        status = draft["job"].get("status")
        if status in FINISHED_STATUSES:
            raise Conflict(f"draft is already {status}")
        if key in self._queue:
            self._queue.remove(key)
        pid = draft["job"].get("pid")
        # Record the cancellation *before* the kill and before the process
        # handle goes away: a concurrent status poll that sees the process
        # gone must find a finished status, not derive ``interrupted``.
        self.store.update_draft_job(design_id, draft_id, status="cancelled", finished_at=_now(), error="cancelled")
        process = self._processes.pop(key, None)
        if process is not None and process.poll() is None:
            self._kill_group(process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL path
                pass
        elif isinstance(pid, int) and _pid_is_workbench_job(pid, self.store.draft_dir(design_id, draft_id)):
            self._kill_group(pid)
        self._pump()
        return self.refresh_draft(design_id, draft_id)

    @staticmethod
    def _kill_group(pid: int) -> None:
        # Jobs run in their own session; never signal a group we belong to.
        try:
            own_group = os.getpgid(pid) == os.getpgid(0)
        except OSError:
            own_group = True
        try:
            if own_group:
                os.kill(pid, signal.SIGTERM)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.05)
        try:
            if own_group:
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    def log_path(self, design_id: str, draft_id: str) -> Path:
        return self.store.draft_dir(design_id, draft_id) / "job.log"

    def stream_log(self, design_id: str, draft_id: str, *, tail: int = 200, follow: bool = True, poll_interval: float = 0.25) -> Iterator[str]:
        """SSE lines (``data: <json string>``) from ``job.log``; closes when the
        job ends. The tail never exceeds ``JOB_LOG_CAP_BYTES``."""

        log_path = self.log_path(design_id, draft_id)
        if not log_path.exists():
            yield "event: end\ndata: \"no log\"\n\n"
            return
        initial = log_path.read_bytes()[-JOB_LOG_CAP_BYTES:]
        complete, sep, remainder = initial.rpartition(b"\n")
        if sep:
            complete += sep
        else:
            complete, remainder = b"", initial
        lines = complete.decode("utf-8", errors="replace").splitlines()
        for line in lines[-tail:] if tail else []:
            yield f"data: {json.dumps(line)}\n\n"
        offset = log_path.stat().st_size
        if not follow:
            try:
                status = self.refresh_draft(design_id, draft_id)["job"].get("status")
            except NotFound:
                return
            if status in FINISHED_STATUSES:
                if remainder:
                    yield f"data: {json.dumps(remainder.decode('utf-8', errors='replace'))}\n\n"
                yield f"event: end\ndata: {json.dumps(status)}\n\n"
            return
        while follow:
            try:
                with log_path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(JOB_LOG_CAP_BYTES)
            except OSError:
                chunk = b""
            if chunk:
                offset += len(chunk)
                parts = (remainder + chunk).split(b"\n")
                remainder = parts.pop()
                for raw in parts:
                    line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
                    yield f"data: {json.dumps(line)}\n\n"
            try:
                draft = self.refresh_draft(design_id, draft_id)
            except NotFound:
                return
            if draft["job"].get("status") in FINISHED_STATUSES and not chunk:
                if remainder:
                    yield f"data: {json.dumps(remainder.decode('utf-8', errors='replace'))}\n\n"
                yield f"event: end\ndata: {json.dumps(draft['job'].get('status'))}\n\n"
                return
            time.sleep(poll_interval)

    # --- reads and saves -------------------------------------------------------

    def get_design(self, design_id: str) -> dict:
        payload = self.store.get_design(design_id)
        payload["drafts"] = [self.refresh_draft(design_id, d["draft_id"]) for d in payload["drafts"]]
        return payload

    def save_revision(self, design_id: str, *, draft_id: str, note: str = "") -> dict:
        self.refresh_draft(design_id, draft_id)
        return self.store.save_revision(design_id, draft_id=draft_id, note=note)

    # --- export (W7, G14) ------------------------------------------------------

    def _export_target(self, design_id: str, rev_id: str) -> tuple[Path, str, dict, dict]:
        """(target dir, published target path, design, revision) for an export.
        The target is always ``<repo_path>/arena/workbench/<design>/<rev>/``:
        never ``cad/``, never outside the instrument repo."""

        design = self.store.read_design(design_id)
        revision = self.store.read_revision(design_id, rev_id)
        repo_dir, repo_path = self._repo_dir(design["instrument_id"], what="exports")
        # Walk from the repo down to the target one component at a time and
        # refuse a symlink at any level (`arena`, `workbench`, the design, the
        # revision): `Path.is_symlink()` only inspects the last component, so
        # a symlinked `arena/` -> `cad/` would otherwise pass unnoticed
        # (Sonnet, #820). The ids passed the store's id rule, so the target
        # cannot leave the repo; the resolve check is belt and braces.
        target = repo_dir
        for part in (*Path(EXPORT_SUBDIR).parts, design_id, rev_id):
            target = target / part
            if target.is_symlink():
                raise Conflict(f"export target has a symlink at {part!r}; refusing to write through it")
        if not target.resolve().is_relative_to(repo_dir):  # pragma: no cover - ids are validated above
            raise NotFound("export target is not inside the instrument repo")
        published = f"{repo_path}/{EXPORT_SUBDIR}/{design_id}/{rev_id}"
        return target, published, design, revision

    def _export_files(self, design: dict, revision: dict, rev_dir: Path) -> list[tuple[str, Optional[Path]]]:
        """(name, source path or None for generated text) in write order."""

        instrument = design["instrument_id"]
        seq = int(revision.get("seq") or 0)
        stem = f"{instrument}-workbench-r{seq}"
        suffix = ".py" if design.get("backend") == "cadquery" else ".scad"
        files: list[tuple[str, Optional[Path]]] = [(f"{stem}{suffix}", rev_dir / revision["source_name"])]
        artifacts = rev_dir / "artifacts"
        for name, ext in (("output.stl", ".stl"), ("preview.png", ".png"), ("model.glb", ".glb")):
            candidate = artifacts / name
            if candidate.is_file() and not candidate.is_symlink():
                files.append((f"{stem}{ext}", candidate))
        files.append(("provenance.json", None))
        files.append(("README.md", None))
        return files

    def export_preview(self, design_id: str, rev_id: str) -> dict:
        """The paths an export would write, and whether the target exists.
        Reads only."""

        target, published, design, revision = self._export_target(design_id, rev_id)
        rev_dir = self.store.revision_dir(design_id, rev_id)
        names = [name for name, _src in self._export_files(design, revision, rev_dir)]
        existing = sorted(p.name for p in target.iterdir() if p.is_file()) if target.is_dir() else []
        return {
            "design_id": design_id,
            "rev_id": rev_id,
            "target": published,
            "files": [{"name": name, "path": f"{published}/{name}", "exists": name in existing} for name in names],
            "exists": bool(existing),
            "existing": existing,
        }

    def export_revision(self, design_id: str, rev_id: str, *, replace: bool = False, voter: str = "tony") -> dict:
        """Copy a revision's source and artifacts into the instrument repo
        under ``arena/workbench/<design>/<rev>/`` with provenance and a README
        that says it is generated. An existing target is a Conflict unless
        ``replace`` is set, and then only that directory's files are replaced.
        Never touches ``cad/``, never runs git; committing stays a human step."""

        target, published, design, revision = self._export_target(design_id, rev_id)
        rev_dir = self.store.revision_dir(design_id, rev_id)
        files = self._export_files(design, revision, rev_dir)
        existing = sorted(p.name for p in target.iterdir() if p.is_file()) if target.is_dir() else []
        if existing and not replace:
            raise Conflict(f"export target already exists: {published} (confirm Replace to overwrite)")
        if target.is_dir():
            for entry in os.scandir(target):
                if entry.is_symlink() or not entry.is_file():
                    raise Conflict("export target holds something that is not a plain file; refusing to replace it")
        target.mkdir(parents=True, exist_ok=True)
        if existing:
            for name in existing:
                (target / name).unlink()
        curation = self.store.curation_state(design_id)
        provenance = {
            "schema": EXPORT_SCHEMA,
            "design_id": design_id,
            "rev_id": rev_id,
            "seq": revision.get("seq"),
            "parent_rev_id": revision.get("parent_rev_id"),
            "instrument_id": design["instrument_id"],
            "backend": design.get("backend"),
            "origin": design.get("origin"),
            "editor": revision.get("editor"),
            "compile": revision.get("compile"),
            "objective": revision.get("objective"),
            "source_sha256": revision.get("source_sha256"),
            "note": revision.get("note"),
            "curation": {"pick": curation.get("pick") == rev_id, "title": curation.get("title"), "note": curation.get("note")},
            "exported_by": voter,
            "exported_at": _now(),
            "generated": True,
        }
        title = curation.get("title") or design.get("title") or design_id
        readme = (
            f"# Workbench revision {revision.get('seq')}: {title}\n\n"
            "**Generated model** from the Arena Studio design workbench (#788). "
            f"Instrument `{design['instrument_id']}`, design `{design_id}`, revision `{rev_id}`"
            f" (parent `{revision.get('parent_rev_id') or 'origin'}`, editor `{(revision.get('editor') or {}).get('kind', 'unknown')}`).\n\n"
            "This is an AI-assisted arena artifact, NOT a measured master or a validated build packet. "
            "See `provenance.json` for the full record. Nothing under `cad/` was changed by this export, "
            "and nothing was committed: commit it in the instrument repo when you're ready.\n"
        )
        written = []
        for name, src in files:
            dest = target / name
            if src is not None:
                shutil.copyfile(src, dest)
            elif name == "provenance.json":
                dest.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            else:
                dest.write_text(readme, encoding="utf-8")
            written.append(f"{published}/{name}")
        return {"design_id": design_id, "rev_id": rev_id, "target": published, "written": written, "replaced": existing}


# --- the job subcommand -------------------------------------------------------


def _log(msg: str) -> None:
    print(msg, flush=True)


def run_job(draft_dir: Path, registry_path: Path) -> int:
    """Body of ``arena workbench-job``: compile one draft in the sandbox,
    score it, and write ``objective.json``, ``params.json`` and the final
    ``job`` block. Returns the process exit code (0 succeeded, 1 candidate
    failed, 2 environment failure)."""

    draft_dir = Path(draft_dir).resolve()
    draft_json = draft_dir / "draft.json"
    if draft_dir.name.startswith("j-") is False or draft_dir.parent.name != "drafts" or not draft_json.is_file():
        _log("ERROR: --draft-dir is not a workbench draft directory")
        return 2
    store = WorkbenchStore(draft_dir.parents[2])
    draft = json.loads(draft_json.read_text(encoding="utf-8"))
    design_id, draft_id = draft["design_id"], draft["draft_id"]
    backend = draft["backend"]
    store.update_draft_job(design_id, draft_id, status="running", pid=os.getpid(), started_at=_now())
    source_path = draft_dir / draft["source_name"]
    out_dir = draft_dir / "artifacts"
    started = time.monotonic()
    _log(f"backend={backend} draft={draft_id} design={design_id}")

    # params.json is pure text analysis: always produced.
    try:
        params = extract_parameters(source_path.read_text(encoding="utf-8", errors="replace"), backend).to_dict()
        (draft_dir / "params.json").write_text(json.dumps(params, indent=2, sort_keys=True), encoding="utf-8")
        _log(f"parameters: {len(params.get('parameters', []))} found")
    except Exception as exc:  # noqa: BLE001 - never fail the compile for this
        _log(f"parameters: unavailable ({exc})")

    try:
        compiler = compiler_for_backend(backend, sandboxed=True)
    except ValueError as exc:
        return _finish(store, design_id, draft_id, "failed", 2, f"sandbox_unavailable: {exc}", started)
    _log("compiling in the sandbox ...")
    try:
        artifacts = compiler(source_path, out_dir)
    except SandboxUnavailable as exc:
        _log(f"ERROR sandbox_unavailable: {exc}")
        return _finish(store, design_id, draft_id, "failed", 2, f"sandbox_unavailable: {exc}", started)
    except render.CompileError as exc:
        text = str(exc)[-_STDERR_LOG_CAP:]
        _log(f"compile failed:\n{text}")
        objective = {
            "schema": "makerbench-workbench-objective-v1", "render_ok": False, "status": "failed",
            "failure_stage": "compile", "error": text, "artifacts": None, "objective": None,
            "sandbox": _sandbox_evidence(backend), "duration_s": round(time.monotonic() - started, 3),
        }
        (draft_dir / "objective.json").write_text(json.dumps(objective, indent=2, sort_keys=True), encoding="utf-8")
        return _finish(store, design_id, draft_id, "failed", 1, text[:2000], started)
    for warning in artifacts.warnings[:50]:
        _log(warning)
    _log(f"compiled: {artifacts.stl_path.name}, {artifacts.png_path.name}")

    design = store.read_design(design_id)
    try:
        registry = load_arena_registry(registry_path)
        spec = next((dict(s) for s in registry["instruments"] if s.get("id") == design["instrument_id"]), None)
    except (OSError, ValueError):
        spec = None
    objective_block: dict[str, Any]
    if spec is None:
        _log("checks: instrument not in the arena registry; gates not declared")
        objective_block = {"declared": False, "note": "instrument not in the arena registry: gates not declared", "checks": {}}
    else:
        context = ObjectiveContext(trial_id=draft_id, model_id="workbench", instrument_id=str(spec["id"]), seed=0,
                                   scad_path=source_path, artifacts=artifacts)
        try:
            gate_result = dict(mesh_objective_gate(spec)(context))
            objective_block = _normalize_gate_result(gate_result)
            objective_block["declared"] = True
            _log(f"checks: pass rate {objective_block.get('objective_pass_rate')}")
        except Exception as exc:  # noqa: BLE001 - a failed gate is a failed draft, not a crash
            _log(f"checks failed: {exc}")
            objective_block = {"declared": True, "error": str(exc)[:2000], "checks": {}}
    objective = {
        "schema": "makerbench-workbench-objective-v1",
        "render_ok": True,
        "status": "scored",
        "failure_stage": None,
        "error": None,
        "artifacts": _artifact_payload(artifacts),
        "objective": objective_block,
        "sandbox": _sandbox_evidence(backend),
        "duration_s": round(time.monotonic() - started, 3),
    }
    (draft_dir / "objective.json").write_text(json.dumps(objective, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return _finish(store, design_id, draft_id, "succeeded", 0, None, started)


def _sandbox_evidence(backend: str) -> dict:
    # Both sandboxed compilers are Bubblewrap; the OpenSCAD one runs Xvfb
    # inside the namespace for the preview.
    return {"kind": "bwrap", "verified": True, "xvfb": backend == "openscad"}


def _finish(store: WorkbenchStore, design_id: str, draft_id: str, status: str, code: int, error: Optional[str], started: float) -> int:
    _log(f"=== {status} in {time.monotonic() - started:.1f}s ===")
    store.update_draft_job(design_id, draft_id, status=status, exit_code=code, finished_at=_now(), error=error)
    return code
