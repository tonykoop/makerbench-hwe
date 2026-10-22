"""Tonight's Queue: confined reads, locked edits, and read-only readiness (#1160)."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .. import nightly_cad, nightly_preflight
from ..run_log_io import atomic_write_json, file_lock
from . import control_room_newrun as nr
from . import doe


class QueueError(ValueError):
    """An unsafe, stale, or inapplicable queue operation."""


def safe_path(path: Path) -> Path:
    """Configured paths are allowed, but never through a symlink (including locks)."""
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise QueueError("Queue paths must not pass through symlinks.")
    return path


def fetch_written_go() -> bool:
    result = subprocess.run(
        ["gh", "api", "--paginate", "repos/tonykoop/makerbench-hwe/issues/664/comments", "--jq", ".[] | .body | @json"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return any(str(json.loads(line)).startswith("GO:") for line in result.stdout.splitlines() if line.strip())


class WrittenGo:
    """Cache successes AND failures for ten minutes, without storing comment bodies."""

    def __init__(self, fetch: Optional[Callable[[], bool]] = None, clock=time.monotonic):
        self.fetch = fetch
        self.clock = clock
        self.checked: Optional[float] = None
        self.value: Optional[bool] = None
        self.lock = threading.Lock()

    def read(self) -> dict:
        with self.lock:
            if self.checked is None or self.clock() - self.checked >= 600:
                self.checked = self.clock()
                try:
                    self.value = (self.fetch or fetch_written_go)()
                except (OSError, subprocess.SubprocessError, ValueError):
                    self.value = None
            return {"given": self.value, "text": "given" if self.value is True else
                    "not given" if self.value is False else "unknown (offline)", "cache_seconds": 600}


def status_lights(repo_root: Path) -> list[dict]:
    """Presence and authenticated pings only. No sign-in, model calls, or tool launches."""
    from ..live_cad_runner import LiveCadConfig, connector_available

    lights = [{"label": "PC awake", "state": "ok", "detail": "Studio is responding"}]
    for label, backend, connector in (("SolidWorks seat", "solidworks-live", "hwe-solidworks"),
                                      ("Fusion seat", "fusion-live", "hwe-fusion")):
        env = {k: v for k, v in os.environ.items() if k.startswith(("HWE_SW_", "HWE_FUSION_"))}
        try:
            ready = connector_available(LiveCadConfig(backend=backend, driver_model="", connector=connector, env=env))
        except (OSError, ValueError):
            ready = False
        lights.append({"label": label, "state": "ok" if ready else "warn",
                       "detail": "authenticated ping answered" if ready else "bridge not reachable"})
    for runtime in ("claude", "codex"):
        present = shutil.which(runtime) is not None
        lights.append({"label": f"{runtime.title()} CLI", "state": "ok" if present else "warn",
                       "detail": "on PATH · sign-in unknown" if present else "not on PATH"})
    for backend, label in (("freecad", "FreeCAD"), ("openscad", "OpenSCAD"), ("blender", "Blender")):
        availability = doe.backend_availability(backend)
        lights.append({"label": label, "state": "ok" if availability["status"] == "available" else "warn",
                       "detail": availability["reason"]})
    for key, label in (("OPENROUTER_API_KEY", "OpenRouter key"), ("HWE_SW_TOKEN", "SolidWorks key"),
                       ("HWE_FUSION_TOKEN", "Fusion key")):
        present = bool(os.environ.get(key))
        lights.append({"label": label, "state": "ok" if present else "warn",
                       "detail": "present" if present else "not in server environment"})
    cadam = nightly_preflight.audit_cadam({}, environ=os.environ)
    lights.append({"label": "CADAM app", "state": "ok" if cadam.server_ok else "warn",
                   "detail": "reachable" if cadam.server_ok else "not reachable"})
    try:
        sessions = doe._load_sessions(str(repo_root / "data" / "sessions.jsonl"))
    except (ValueError, TypeError):
        sessions = []
    for runtime in ("claude", "codex"):
        observed = next((s.telemetry.get("weekly_quota", {}).get(runtime)
                         for s in reversed(sessions) if isinstance(s.telemetry.get("weekly_quota"), dict)
                         and isinstance(s.telemetry["weekly_quota"].get(runtime), dict)), {})
        value = observed.get("remaining_percent")
        known = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100
        lights.append({"label": f"{runtime.title()} weekly quota", "state": "unknown",
                       "detail": f"{value:g}% remaining (recorded telemetry)" if known else "unknown"})
    return lights


class QueueStore:
    def __init__(self, *, runs_root: Path, queue_path: Path, repo_root: Path, writes: bool = False,
                 instruments_root: Optional[Path] = None):
        self.runs_root, self.queue_path, self.repo_root = map(Path, (runs_root, queue_path, repo_root))
        self.writes, self.instruments_root = writes, instruments_root
        self._lock = threading.RLock()
        self._source = None
        self._draft = None
        self.go = WrittenGo()
        self._lights = None
        self._lights_checked = 0.0

    def check_paths(self):
        try:
            nr.confined(self.runs_root, "control-room")
            safe_path(self.queue_path)
            safe_path(self.queue_path.with_name(self.queue_path.name + ".lock"))
        except nr.UnsafePath as exc:
            raise QueueError("Control Room paths must not pass through symlinks.") from exc

    def _read(self):
        self.check_paths()
        try:
            raw = self.queue_path.read_bytes()
            payload = json.loads(raw)
        except FileNotFoundError:
            raw, payload = b"", {"schema": nightly_cad.SCHEMA, "jobs": []}
        except (OSError, ValueError):
            raise QueueError("The configured nightly queue is unreadable.") from None
        if (not isinstance(payload, dict) or payload.get("schema") != nightly_cad.SCHEMA
                or not isinstance(payload.get("jobs"), list)
                or any(not isinstance(j, dict) or not isinstance(j.get("job_id"), str) or not j["job_id"]
                       or not isinstance(j.get("entrants", []), list)
                       or any(not isinstance(e, dict) for e in j.get("entrants", [])) for j in payload["jobs"])):
            raise QueueError("The configured nightly queue has an invalid shape.")
        ids = [j["job_id"] for j in payload["jobs"]]
        if len(ids) != len(set(ids)):
            raise QueueError("The configured nightly queue has duplicate job ids.")
        if not self.writes:
            source = hashlib.sha256(raw).hexdigest()
            if self._source != source:
                self._source, self._draft = source, copy.deepcopy(payload)
            payload = self._draft
        return payload

    @staticmethod
    def revision(payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def plan(self, job_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id) or job_id in {".", ".."}:
            return {}
        try:
            path = nr.confined(self.runs_root, "control-room", "plans", f"{job_id}.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (nr.UnsafePath, OSError, ValueError):
            return {}

    def reference(self, job_id: str) -> Optional[Path]:
        with self._lock:
            job = next((j for j in self._read()["jobs"] if j["job_id"] == job_id), None)
            if job is None:
                return None
            path = Path(str(job.get("reference_image") or ""))
            if not path.is_absolute():
                path = self.runs_root / path
            plans = self.runs_root / "control-room" / "plans"
            try:
                path = safe_path(path)
                nr.confined(self.runs_root, "control-room", "plans")
                if not path.resolve().is_relative_to(plans.resolve()) or not path.is_file():
                    return None
                if path.stat().st_size > nr.MAX_REFERENCE_BYTES:
                    return None
                with path.open("rb") as stream:
                    magic = stream.read(16)
                return path if any(magic.startswith(m) for m in nr.IMAGE_MAGIC) else None
            except (QueueError, nr.UnsafePath, OSError):
                return None

    def view(self, instruments=()) -> dict:
        with self._lock:
            payload = self._read()
            cards = {i["id"]: i for i in instruments}
            jobs = []
            for raw in payload["jobs"]:
                plan = self.plan(raw["job_id"])
                card = cards.get(raw.get("instrument_id"), {})
                entrants = []
                for item in raw.get("entrants") or []:
                    try:
                        label = nightly_cad.route_label(nightly_cad.NightlyEntrant.from_dict(item))
                    except (ValueError, TypeError):
                        label = "Unsupported route"
                    entrants.append({"id": item.get("entrant_id", ""), "label": label})
                status = raw.get("status") or "queued"
                display_status = "done" if status in {"votable", "complete", "completed"} else status
                if payload.get("draft") and status == "queued":
                    display_status = "draft"
                jobs.append({"job_id": raw["job_id"], "instrument_id": raw.get("instrument_id"),
                             "name": card.get("display_name") or raw.get("instrument_id"), "family": card.get("family", ""),
                             "status": status, "display_status": display_status,
                             "deliverable": raw.get("deliverable") or plan.get("deliverable") or "single",
                             "cap_usd": raw.get("budget_usd", 0), "entrants": entrants,
                             "drafted_by": raw.get("drafted_by") or plan.get("drafted_by") or payload.get("drafted_by") or "Not recorded",
                             "run_id": raw.get("run_id"), "reason": raw.get("blocked_reason") or raw.get("error"),
                             "has_reference": self.reference(raw["job_id"]) is not None,
                             "can_undo": status == "removed" and raw.get("queue_previous_status") in {"queued", "draft"}})
            return {"jobs": jobs, "revision": self.revision(payload), "queue_writes": self.writes,
                    "queue_name": self.queue_path.name, "exists": self.queue_path.is_file()}

    def mutate(self, *, action: str, job_id: str, revision: str, order: list[str]):
        with self._lock:
            self.check_paths()
            if self.writes:
                with file_lock(self.queue_path):
                    self._apply(self._read(), action, job_id, revision, order)
            else:
                self._apply(self._read(), action, job_id, revision, order)

    def _apply(self, payload, action, job_id, revision, order):
        if revision != self.revision(payload):
            raise QueueError("The queue changed. Refresh before editing it.")
        jobs = payload["jobs"]
        editable = [j for j in jobs if (j.get("status") or "queued") in {"queued", "draft"}]
        job = next((j for j in jobs if j["job_id"] == job_id), None)
        if action == "reorder":
            if len(order) != len(editable) or set(order) != {j["job_id"] for j in editable}:
                raise QueueError("Reorder must contain every queued job exactly once.")
        elif action == "tomorrow":
            if job not in editable:
                raise QueueError("Only queued nights can move to tomorrow (the end of the queue).")
            order = [j["job_id"] for j in editable if j is not job] + [job_id]
        elif action == "remove":
            if job not in editable:
                raise QueueError("Only queued nights can be removed.")
            job["queue_previous_status"] = job.get("status") or "queued"
            job["status"] = "removed"
        elif action == "undo":
            if job is None or job.get("status") != "removed" or job.get("queue_previous_status") not in {"queued", "draft"}:
                raise QueueError("This removal cannot be undone.")
            job["status"] = job.pop("queue_previous_status")
        else:
            raise QueueError("Unknown queue action.")
        if action in {"reorder", "tomorrow"}:
            by_id = {j["job_id"]: j for j in editable}
            replacement = iter(by_id[i] for i in order)
            payload["jobs"] = [next(replacement) if j in editable else j for j in jobs]
        if self.writes:
            self.check_paths()
            atomic_write_json(self.queue_path, payload)

    def readiness(self) -> dict:
        self.check_paths()
        task = "unknown (Windows task)"
        try:
            path = nr.confined(self.runs_root, "control-room", "task-state.json")
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("state", "").lower() in {"enabled", "disabled"}:
                task = f"{cached['state'].title()} (cached)"
        except (nr.UnsafePath, OSError, ValueError, AttributeError):
            pass
        with self._lock:
            if self._lights is None or time.monotonic() - self._lights_checked >= 60:
                self._lights = status_lights(self.repo_root)
                self._lights_checked = time.monotonic()
        return {"task": task, "written_go": self.go.read(), "lights": self._lights}

    def preflight(self, job_id: str) -> dict:
        with self._lock:
            selected = next((j for j in self._read()["jobs"] if j["job_id"] == job_id), None)
            if selected is None or selected.get("status") in {"running", "removed"}:
                raise QueueError("Select a queued or blocked night for preflight.")
            job = copy.deepcopy(selected)
        job["status"] = "queued"  # a temporary selected-job snapshot, never the live queue
        if not Path(job.get("reference_image") or "").is_absolute():
            job["reference_image"] = str(self.runs_root / str(job.get("reference_image") or ""))
        with tempfile.TemporaryDirectory(prefix="makerbench-queue-preflight-") as folder:
            queue = Path(folder) / "selected.json"
            atomic_write_json(queue, {"schema": nightly_cad.SCHEMA, "jobs": [job]})
            paths = {"repo_root": self.repo_root, "queue": queue, "output_root": self.runs_root,
                     "runner_script": self.repo_root / "scripts" / "windows" / "run-nightly-cad-arena.ps1"}
            if self.instruments_root:
                paths["instruments_root"] = self.instruments_root
            report = nightly_preflight.build_report(
                secrets_path=self.queue_path.parent / "makerbench-nightly.env", queue_path=queue,
                lock_path=self.runs_root / ".nightly-cad.lock", named_paths=paths, check_entrants=True,
            )
        return {"job_id": job_id, "ok": report.ok,
                "checks": [{"label": "Secrets present", "ok": report.secrets_ok},
                           {"label": "Nightly seat lock", "ok": report.lock.ok},
                           {"label": "Selected queue job", "ok": report.queue.ok},
                           {"label": "Required paths", "ok": report.paths_ok},
                           {"label": "CADAM readiness", "ok": report.cadam.ok if report.cadam else True},
                           *({"label": route.label, "ok": route.ok} for route in report.routes)],
                "entrants": [{"id": e.entrant_id, "route": e.route, "ok": e.ok, "notes": list(e.notes)}
                             for e in report.entrants.entrants] if report.entrants else [],
                "notes": list(report.entrants.job_notes) if report.entrants else []}
