"""Resumable, budgeted overnight orchestration for the Code-CAD Arena."""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import re
import shutil
import socket
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from . import cad_names
from . import code_cad_arena_runner as arena_runner
from . import code_cad_providers as providers
from .cadam_adapter import CadamClient, CadamConfig, CadamRecoveryRequiredError, CadamStreamFailedError
from .code_cad_orchestrator import OrchestrationConfig, build_trial_matrix, run_orchestration
from .live_cad_runner import (
    LiveCadConfig,
    connector_available,
    connector_part_document,
    connector_mcp_probe,
    connector_unattended,
    driver_route,
    make_live_execute_trial,
)
from . import cadam_routes
from . import nightly_revisions as revisions
from .run_log_io import atomic_write_json, file_lock, find_trial, has_trial, trial_rows
from .schema import MatchupMetadata, matchup_metadata


class LiveSeatUnavailableError(RuntimeError):
    """The live entrant cannot start because its seat failed preflight."""


SCHEMA = "makerbench-nightly-cad-queue-v1"
STATE_SCHEMA = "makerbench-nightly-cad-state-v1"
MORNING_SCHEMA = "makerbench-nightly-cad-morning-v1"
BRIEF_SCHEMA = "makerbench-nightly-cad-brief-v1"
MAX_TURNS = 8
DEFAULT_TURNS = 3
#: What a night builds. Assembly nights need an assembly harness the runner does not have yet.
DELIVERABLES = ("single",)
REFERENCE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_BACKEND_LABELS = {"openscad": "OpenSCAD", "freecad": "FreeCAD", "cadquery": "CadQuery", "blender": "Blender",
                   "solidworks-live": "SolidWorks live bridge", "fusion-live": "Fusion live bridge"}
_RUNTIME_LABELS = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "agy": "Antigravity",
                   "openrouter": "OpenRouter", "stub": "Stub"}


@dataclass(frozen=True)
class NightlyEntrant:
    entrant_id: str
    kind: str
    model_id: str
    backend: str = "openscad"
    context_tier: str = "blind"
    max_cost_usd: float = 0.0
    timeout_s: Optional[int] = None
    # CADAM entrants only (#1102): which model route serves the turn, and for the CLI bridge
    # which subscription runtime backs it. Reported as separate entrants, never merged.
    route: str = cadam_routes.ROUTE_OPENROUTER
    runtime: str = ""

    @property
    def route_label(self) -> Optional[str]:
        if self.kind != "cadam":
            return None
        return cadam_routes.route_label(self.route, self.runtime, self.model_id)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NightlyEntrant":
        return cls(
            entrant_id=str(payload.get("entrant_id") or payload.get("model_id") or ""),
            kind=str(payload.get("kind") or "arena"),
            model_id=str(payload.get("model_id") or ""),
            backend=str(payload.get("backend") or "openscad"),
            context_tier=str(payload.get("context_tier") or "blind"),
            max_cost_usd=float(payload.get("max_cost_usd") or 0.0),
            timeout_s=(int(payload["timeout_s"]) if payload.get("timeout_s") is not None else None),
            route=str(payload.get("route") or cadam_routes.ROUTE_OPENROUTER),
            runtime=str(payload.get("runtime") or ""),
        )

    def matchup_level(self) -> str:
        """Read a verifiable level from model::level[::backend], never a plain ID."""
        parts = self.entrant_id.split("::")
        if (len(parts) not in {2, 3} or parts[0] != self.model_id
                or not parts[1] or parts[1] != parts[1].strip()
                or (len(parts) == 3 and parts[2] != self.backend)):
            raise ValueError("matchup level provenance requires model::level[::backend] entrant IDs")
        return parts[1]

    def validate(self) -> None:
        if not self.entrant_id or not self.model_id:
            raise ValueError("nightly entrants require entrant_id and model_id")
        if self.kind not in {"arena", "cadam", "live"}:
            raise ValueError(f"unsupported nightly entrant kind: {self.kind}")
        if self.kind == "live" and self.backend not in {"solidworks-live", "fusion-live"}:
            raise ValueError("live entrants require solidworks-live or fusion-live backend")
        if self.max_cost_usd < 0:
            raise ValueError("max_cost_usd cannot be negative")
        if self.route not in cadam_routes.ROUTES:
            raise ValueError(f"unsupported CADAM route: {self.route}")
        if self.kind != "cadam" and (self.route != cadam_routes.ROUTE_OPENROUTER or self.runtime):
            raise ValueError("route and runtime apply to cadam entrants only")
        if self.kind == "cadam" and self.route == cadam_routes.ROUTE_CLI_BRIDGE and self.runtime not in cadam_routes.RUNTIMES:
            raise ValueError("cli-bridge CADAM entrants need runtime claude or codex")
        if self.kind == "cadam" and self.route == cadam_routes.ROUTE_OPENROUTER and self.runtime:
            raise ValueError("runtime applies to the cli-bridge route only")


@dataclass
class NightlyJob:
    job_id: str
    instrument_id: str
    reference_image: str
    entrants: list[NightlyEntrant]
    seed: int = 0
    budget_usd: float = 5.0
    status: str = "queued"
    run_id: Optional[str] = None
    run_dir: Optional[str] = None
    # A Control Room revision (#1116): one entrant continued from its best turn with feedback.
    # Never read from the nightly queue file; the executor builds it from the revision queue.
    revision: Optional[dict] = None
    deliverable: str = "single"
    turns: int = DEFAULT_TURNS
    stop_early: bool = True

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NightlyJob":
        return cls(
            job_id=str(payload.get("job_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            reference_image=str(payload.get("reference_image") or ""),
            entrants=[
                NightlyEntrant.from_dict(item)
                for item in payload.get("entrants") or []
                if isinstance(item, Mapping)
            ],
            seed=int(payload.get("seed") or 0),
            budget_usd=float(payload.get("budget_usd") or 5.0),
            status=str(payload.get("status") or "queued"),
            run_id=str(payload["run_id"]) if payload.get("run_id") else None,
            run_dir=str(payload["run_dir"]) if payload.get("run_dir") else None,
            deliverable=str(payload.get("deliverable") or "single"),
            # Existing queues predate grader-driven revision and retain one attempt.
            turns=payload.get("turns", 1),
            stop_early=payload.get("stop_early", True),
        )

    def validate(self) -> None:
        if not self.job_id or not self.instrument_id:
            raise ValueError("nightly jobs require job_id and instrument_id")
        if self.reference_image or not self.revision:
            if not Path(self.reference_image).is_file():
                raise ValueError(f"nightly reference image is missing: {self.reference_image}")
        if len(self.entrants) < (1 if self.revision else 2):
            raise ValueError("nightly jobs require at least two entrants")
        if self.budget_usd < 0:
            raise ValueError("budget_usd cannot be negative")
        if isinstance(self.turns, bool) or not isinstance(self.turns, int) or not 1 <= self.turns <= MAX_TURNS:
            raise ValueError(f"turns must be a whole number from 1 to {MAX_TURNS}")
        if not isinstance(self.stop_early, bool):
            raise ValueError("stop_early must be a boolean")
        if self.deliverable == "assembly":
            raise ValueError("assembly nights are not supported by the nightly runner yet; queue a single-part night")
        if self.deliverable not in DELIVERABLES:
            raise ValueError(f"unknown nightly deliverable {self.deliverable!r} (expected one of {DELIVERABLES})")
        for entrant in self.entrants:
            entrant.validate()

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "instrument_id": self.instrument_id,
            "reference_image": self.reference_image,
            "seed": self.seed,
            "budget_usd": self.budget_usd,
            "status": self.status,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "deliverable": self.deliverable,
            "turns": self.turns,
            "stop_early": self.stop_early,
            "entrants": [entrant.__dict__ for entrant in self.entrants],
        }


@dataclass
class BudgetGuard:
    limit_usd: float
    spent_usd: float = 0.0
    charges: list[dict] = field(default_factory=list)
    reservations: dict[str, float] = field(default_factory=dict)
    dispatches: dict[str, dict] = field(default_factory=dict)
    halted_reason: Optional[str] = None

    def reserve(self, entrant: NightlyEntrant) -> None:
        if self.halted_reason:
            raise RuntimeError(f"nightly spending halted: {self.halted_reason}")
        if entrant.entrant_id in self.reservations:
            return
        if entrant.max_cost_usd > self.remaining_usd:
            raise RuntimeError(
                f"paid entrant {entrant.entrant_id} maximum ${entrant.max_cost_usd:.2f} "
                f"exceeds remaining nightly budget ${self.remaining_usd:.2f}"
            )
        if entrant.max_cost_usd:
            self.reservations[entrant.entrant_id] = round(entrant.max_cost_usd, 4)

    def charge(self, entrant: NightlyEntrant, amount_usd: float) -> Optional[str]:
        for charge in self.charges:
            if charge.get("entrant_id") == entrant.entrant_id:
                return str(charge.get("violation")) if charge.get("violation") else None
        amount = max(0.0, float(amount_usd))
        self.reservations.pop(entrant.entrant_id, None)
        self.spent_usd = round(self.spent_usd + amount, 4)
        violation = None
        if entrant.max_cost_usd and amount > entrant.max_cost_usd:
            violation = (
                f"entrant {entrant.entrant_id} actual ${amount:.4f} exceeded its "
                f"${entrant.max_cost_usd:.4f} ceiling"
            )
        if self.spent_usd > self.limit_usd:
            violation = (
                f"nightly actual ${self.spent_usd:.4f} exceeded the "
                f"${self.limit_usd:.4f} budget"
            )
        record = {
            "entrant_id": entrant.entrant_id,
            "cost_usd": amount,
            "max_cost_usd": entrant.max_cost_usd,
        }
        if violation:
            record["violation"] = violation
            self.halted_reason = violation
        self.charges.append(record)
        return violation

    def prepare_dispatch(self, job: NightlyJob, entrant: NightlyEntrant) -> dict:
        existing = self.dispatches.get(entrant.entrant_id)
        if existing:
            return existing
        conversation_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"makerbench:{job.run_id}:{job.job_id}:{entrant.entrant_id}",
            )
        )
        dispatch = {
            "conversation_id": conversation_id,
            "status": "prepared",
            "max_cost_usd": entrant.max_cost_usd,
        }
        self.dispatches[entrant.entrant_id] = dispatch
        return dispatch

    @property
    def remaining_usd(self) -> float:
        reserved = sum(self.reservations.values())
        return max(0.0, round(self.limit_usd - self.spent_usd - reserved, 4))


def _resume_budget(run_dir: Path, *, limit_usd: float) -> BudgetGuard:
    """Restore recorded charges so a restarted night cannot spend them twice."""

    state_path = Path(run_dir) / "nightly-state.json"
    if not state_path.is_file():
        return BudgetGuard(limit_usd)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    saved = state.get("budget")
    if not isinstance(saved, Mapping):
        return BudgetGuard(limit_usd)
    charges = saved.get("charges")
    reservations = saved.get("reservations")
    dispatches = saved.get("dispatches")
    return BudgetGuard(
        limit_usd=limit_usd,
        spent_usd=max(0.0, float(saved.get("spent_usd") or 0.0)),
        charges=[dict(item) for item in charges or [] if isinstance(item, Mapping)],
        reservations={
            str(key): float(value)
            for key, value in (reservations or {}).items()
        }
        if isinstance(reservations, Mapping)
        else {},
        dispatches={
            str(key): dict(value)
            for key, value in (dispatches or {}).items()
            if isinstance(value, Mapping)
        }
        if isinstance(dispatches, Mapping)
        else {},
        halted_reason=(
            str(saved["halted_reason"])
            if saved.get("halted_reason")
            else None
        ),
    )


def load_queue(path: Path) -> tuple[dict, list[NightlyJob]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"nightly queue schema must be {SCHEMA}")
    jobs = [NightlyJob.from_dict(item) for item in payload.get("jobs") or []]
    return payload, jobs


def save_queue(path: Path, payload: Mapping[str, object], jobs: list[NightlyJob]) -> None:
    updated = dict(payload)
    updated["jobs"] = [job.as_dict() for job in jobs]
    atomic_write_json(Path(path), updated)


class QueueClaimChanged(RuntimeError):
    """The queued selection changed; the runner must read and claim again."""


def save_active_job(path: Path, job: NightlyJob) -> None:
    """Persist only runner-owned state, preserving concurrent queue edits (#1160).

    Tonight's Queue and New Run use this same queue sidecar lock. A job removed
    before its claim is persisted must never be resurrected or dispatched.
    """
    with file_lock(path):
        payload, _jobs = load_queue(path)
        current = next((row for row in payload["jobs"] if row.get("job_id") == job.job_id), None)
        if current is None or (current.get("status") or "queued") not in {"queued", "running"}:
            error = QueueClaimChanged if job.status == "running" else RuntimeError
            raise error("The selected nightly job changed before its state could be saved.")
        if job.status == "running" and (current.get("status") or "queued") == "queued":
            next_job = next((row for row in payload["jobs"] if (row.get("status") or "queued") in {"queued", "running"}), None)
            if next_job is not current:
                raise QueueClaimChanged("The nightly queue order changed before this job could be claimed.")
        current.update(status=job.status, run_id=job.run_id, run_dir=job.run_dir)
        atomic_write_json(path, payload)


@contextmanager
def nightly_lease(path: Path, *, now: Callable[[], datetime]) -> Iterator[None]:
    """Acquire the single-seat nightly lease without waiting."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another nightly CAD run holds {path}") from exc
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "heartbeat_utc": now().astimezone(timezone.utc).isoformat(),
            },
            handle,
        )
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def night_stem(job: "NightlyJob") -> str:
    """``<instrument>_<variant>_<revision>`` for the night's deliverables (#1134).

    A nightly job builds the instrument as registered (variant ``base``); a Morning Review
    revision is the design's next revision.
    """
    return cad_names.stem(job.instrument_id, cad_names.BASE_VARIANT, "r2" if job.revision else "r1")


def _model_label(name: str) -> str:
    """``anthropic/claude-opus-5.5`` / ``claude-sonnet-5-5`` / ``gpt-6.1-sol`` -> ``Opus 5.5`` / ``Sonnet 5.5`` / ``GPT-6.1 Sol``."""
    bare = name.rsplit("/", 1)[-1]
    family = re.match(r"^(?:claude-)?(fable|opus|sonnet|haiku)-(\d+)[.-](\d+)$", bare, re.I)
    if family:
        return f"{family.group(1).title()} {family.group(2)}.{family.group(3)}"
    gpt = re.match(r"^gpt-([0-9.]+)(?:-(.+))?$", bare, re.I)
    if gpt:
        return f"GPT-{gpt.group(1)}" + (f" {gpt.group(2).replace('-', ' ').title()}" if gpt.group(2) else "")
    return bare


def route_label(entrant: NightlyEntrant) -> str:
    """The route Morning Review shows for an entrant, e.g. ``CADAM · OpenRouter · Opus 5.5`` (#1099).

    Total: an id the runner cannot route (the same reason that entrant's dispatch failed) gets an
    honest fallback label instead of raising, so labelling can never abort the night (#648).
    """
    try:
        return _route_label(entrant)
    except (ValueError, KeyError):
        return (f"{entrant.kind} · {entrant.model_id or 'no model'} · "
                f"{_BACKEND_LABELS.get(entrant.backend, entrant.backend)} (route not recognized)")


def _route_label(entrant: NightlyEntrant) -> str:
    if entrant.kind == "cadam":  # route-aware (#1102): "CADAM · OpenRouter · M" or "CADAM via CLI · <runtime> · M"
        return cadam_routes.route_label(entrant.route, entrant.runtime, _model_label(entrant.model_id))
    if entrant.kind == "live":
        runtime, model = driver_route(entrant.model_id)
        return f"{_RUNTIME_LABELS[runtime]} · {_model_label(model)} · {_BACKEND_LABELS.get(entrant.backend, entrant.backend)}"
    provider = providers.provider_for_model_id(entrant.model_id)
    model = providers.model_name_for_model_id(entrant.model_id, provider) or entrant.model_id
    return (f"{_RUNTIME_LABELS.get(provider, provider)} · {_model_label(model)} · "
            f"{_BACKEND_LABELS.get(entrant.backend, entrant.backend)}")


def _night_inputs(run_dir: Path, job: NightlyJob, registry: Mapping[str, object], *, night: str) -> None:
    """``brief.json`` and a ``reference.*`` copy in the run folder, for Morning Review (#1105).

    Written once, at the first claim; a resumed night keeps what it started with.
    """
    brief_path = run_dir / "brief.json"
    reference_name = None
    source = Path(job.reference_image) if job.reference_image else None
    if source is not None and source.is_file():
        suffix = source.suffix.lower()
        if suffix in REFERENCE_SUFFIXES:
            reference_name = f"reference{suffix}"
            target = run_dir / reference_name
            if not target.exists():
                shutil.copyfile(source, target)
        else:
            reference_name = "reference.png"
            target = run_dir / reference_name
            if not target.exists():
                from PIL import Image

                with Image.open(source) as image:
                    image.convert("RGB").save(target, format="PNG", optimize=True)
    if brief_path.exists():
        return
    spec = arena_runner.instrument_spec_from_registry(registry, job.instrument_id)
    atomic_write_json(brief_path, {
        "schema": BRIEF_SCHEMA,
        "night": night,
        "instrument_id": job.instrument_id,
        "title": str(spec.get("display_name") or spec.get("name") or job.instrument_id),
        "brief": str(spec.get("task_brief") or spec.get("task_brief_short") or ""),
        "brief_version": int(spec.get("brief_version") or 1),  # #1150: graded joints arrived in v2
        "deliverable": job.deliverable,
        "reference_image": reference_name,
    })  # no entrant identities here: the folder also holds the blind vote pages


def _record_native(run_log_path: Path, job: NightlyJob, entrant: NightlyEntrant, path: Path) -> None:
    """Point the trial's ``result.native_path`` at its #1134-named deliverable."""
    trial_id = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    with file_lock(run_log_path):
        payload = json.loads(run_log_path.read_text(encoding="utf-8"))
        row = find_trial(payload, trial_id)
        result = row.get("result") if isinstance(row, dict) else None
        if not isinstance(result, dict):
            return
        result["native_path"] = Path(path).as_posix()
        atomic_write_json(run_log_path, payload)


def _record_route(run_log_path: Path, job: NightlyJob, entrant: NightlyEntrant) -> None:
    """Stamp the entrant's route on its trial row (``meta.route``), which Morning Review shows."""
    trial_id = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    with file_lock(run_log_path):
        payload = json.loads(run_log_path.read_text(encoding="utf-8"))
        row = find_trial(payload, trial_id)
        if not isinstance(row, dict):
            return
        meta = row.setdefault("meta", {})
        if not isinstance(meta, dict) or meta.get("route"):
            return
        meta["route"] = route_label(entrant)
        atomic_write_json(run_log_path, payload)


def _fresh_run_log(run_dir: Path, job: NightlyJob, *,
                   matchup: Optional[Mapping[str, object]] = None) -> None:
    path = run_dir / "run_log.json"
    normalized = MatchupMetadata.model_validate(matchup).model_dump(mode="json") if matchup else None
    if normalized:
        if not job.entrants:
            raise ValueError("matchup level provenance requires encoded entrants")
        observed = {"instruments": [job.instrument_id], "seeds": [job.seed],
                    "backends": [e.backend for e in job.entrants],
                    "context_tiers": [e.context_tier for e in job.entrants],
                    "models": [e.model_id for e in job.entrants if e.kind != "live"],
                    "driver_models": [e.model_id for e in job.entrants if e.kind == "live"],
                    "levels": [e.matchup_level() for e in job.entrants]}
        for axis, values in observed.items():
            allowed = normalized["values"] if axis == normalized["varied_axis"] else (
                [normalized["held"][axis]] if axis in normalized["held"] else None)
            if allowed is not None and any(value not in allowed for value in values):
                raise ValueError(f"matchup {axis} provenance disagrees with the queued job")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8")).get("config", {}).get("matchup")
        if existing != normalized:
            raise ValueError("matchup provenance cannot change when resuming a run")
        return
    atomic_write_json(
        path,
        {
            "schema": arena_runner.SCHEMA,
            "config": {
                "instrument_ids": [job.instrument_id],
                "model_ids": [entrant.entrant_id for entrant in job.entrants],
                "seeds": [job.seed],
                "reps": 1,
                "max_attempts": 1,
                "model_providers": {entrant.entrant_id: entrant.kind for entrant in job.entrants},
                "provider_rate_limits_s": {},
                "backend": "nightly-mixed",
                **({"matchup": normalized} if normalized else {}),
            },
            "trials": [],
            "summary": {"counts": {}, "total_trials": 0, "total_attempts": 0},
        },
    )


def _has_trial(run_log_path: Path, job: NightlyJob, entrant: NightlyEntrant) -> bool:
    payload = json.loads(run_log_path.read_text(encoding="utf-8"))
    expected = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    return has_trial(payload, expected)


def _trial_cost(run_log_path: Path, job: NightlyJob, entrant: NightlyEntrant) -> float:
    """Recover a completed entrant's durable cost from its provenance record."""

    payload = json.loads(run_log_path.read_text(encoding="utf-8"))
    expected = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    row = find_trial(payload, expected)
    if not isinstance(row, Mapping):
        return 0.0
    result = row.get("result")
    turns = (result or row.get("meta") or {}).get("turns")
    if turns:
        return sum(max(0.0, float(turn.get("cost_usd") or 0.0)) for turn in turns)
    gen = result.get("gen") if isinstance(result, Mapping) else None
    provenance_path = gen.get("provenance_path") if isinstance(gen, Mapping) else None
    if not provenance_path or not Path(str(provenance_path)).is_file():
        return 0.0
    provenance = json.loads(Path(str(provenance_path)).read_text(encoding="utf-8"))
    return max(0.0, float(provenance.get("cost_usd") or 0.0))


def _append_error_trial(
    run_log_path: Path,
    job: NightlyJob,
    entrant: NightlyEntrant,
    error: str,
) -> None:
    trial_id = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    with file_lock(run_log_path):
        payload = json.loads(run_log_path.read_text(encoding="utf-8"))
        if has_trial(payload, trial_id):
            return
        payload.setdefault("trials", []).append(
            {
                "trial_id": trial_id,
                "instrument_id": job.instrument_id,
                "model_id": entrant.entrant_id,
                "seed": job.seed,
                "rep": 0,
                "provider": entrant.kind,
                "status": "error",
                "attempts": 1,
                "result": None,
                "error": error,
            }
        )
        counts: dict[str, int] = {}
        for row in trial_rows(payload):
            status = str(row.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
        payload.setdefault("summary", {})["counts"] = counts
        atomic_write_json(run_log_path, payload)


def finalize_morning_bundle(run_dir: Path, *, cost_usd: float) -> dict:
    """Create anonymous turntable vote pages and separate reveal metadata."""

    from .cli_arena import _stage_blind_assets
    from .code_cad_vote_surface import build_blind_pair, render_vote_surface

    run_dir = Path(run_dir)
    run_log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    cells = arena_runner.build_vote_candidates(run_log)
    rows_by_id = {str(row.get("trial_id")): row for row in trial_rows(run_log)}
    vote_pages = run_dir / "morning-vote"
    vote_pages.mkdir(parents=True, exist_ok=True)
    pair_files: list[str] = []
    reveals: list[dict] = []
    valid_ids: set[str] = set()
    pair_index = 0
    for cell, candidates in sorted(cells.items()):
        valid_ids.update(candidate.candidate_id for candidate in candidates)
        for left_raw, right_raw in itertools.combinations(candidates, 2):
            hint = f"night-{pair_index:03d}"
            left = _stage_blind_assets(left_raw, hint, "left", vote_pages)
            right = _stage_blind_assets(right_raw, hint, "right", vote_pages)
            pair = build_blind_pair(left, right, pair_seed=f"{run_dir.name}:{cell}:{pair_index}")
            filename = f"pair-{pair_index:03d}.html"
            (vote_pages / filename).write_text(render_vote_surface(pair), encoding="utf-8")
            pair_files.append(filename)
            reveals.append(
                {
                    "pair_id": pair.pair_id,
                    "instrument_id": cell[0],
                    "seed": cell[1],
                    "rep": cell[2],
                    "left": {
                        "trial_id": pair.left.trial_id,
                        "model_id": pair.left.model_id,
                        "result": rows_by_id.get(pair.left.trial_id, {}).get("result"),
                    },
                    "right": {
                        "trial_id": pair.right.trial_id,
                        "model_id": pair.right.model_id,
                        "result": rows_by_id.get(pair.right.trial_id, {}).get("result"),
                    },
                }
            )
            pair_index += 1

    failures = [
        {
            "trial_id": row.get("trial_id"),
            "status": row.get("status"),
            "error": row.get("error"),
        }
        for row in trial_rows(run_log)
        if row.get("trial_id") not in valid_ids
    ]
    summary = {
        "schema": MORNING_SCHEMA,
        "run_id": run_dir.name,
        "votable": len(valid_ids) >= 2 and bool(pair_files),
        "valid_candidate_count": len(valid_ids),
        "failed_candidate_count": len(failures),
        "failures": failures,
        "pair_files": pair_files,
        "cost_usd": round(cost_usd, 4),
    }
    atomic_write_json(run_dir / "morning-summary.json", summary)
    private_root = run_dir.parent / ".makerbench-private"
    private_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        private_root / f"{run_dir.name}-reveal.json",
        {"schema": "makerbench-nightly-cad-reveal-v1", "pairs": reveals, "failures": failures},
    )
    links = "\n".join(
        f'<li><a href="morning-vote/{name}">Blind pair {index + 1}</a></li>'
        for index, name in enumerate(pair_files)
    )
    (run_dir / "morning.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>Morning CAD vote</title>'
        "<style>body{font:18px system-ui;max-width:760px;margin:3rem auto;padding:1rem}"
        "li{margin:.8rem 0}</style></head><body><h1>Morning CAD vote</h1>"
        f"<p>{len(valid_ids)} anonymous candidates · ${cost_usd:.2f} overnight spend</p>"
        f"<ol>{links}</ol></body></html>",
        encoding="utf-8",
    )
    return summary


class NightlyExecutor:
    def __init__(
        self,
        *,
        queue_path: Path,
        registry_path: Path,
        output_root: Path,
        instruments_root: Optional[Path] = None,
        lease_path: Optional[Path] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        runs_root: Optional[Path] = None,
        handlers: Optional[
            Mapping[
                str,
                Callable[[NightlyJob, NightlyEntrant, Path, Mapping[str, object]], float],
            ]
        ] = None,
    ) -> None:
        self.queue_path = Path(queue_path)
        self.registry_path = Path(registry_path)
        self.output_root = Path(output_root)
        self.instruments_root = Path(instruments_root) if instruments_root else None
        self.lease_path = Path(lease_path or self.output_root / ".nightly-cad.lock")
        self.now = now
        self.handlers = dict(handlers or {})
        # Where ``control-room/revision-queue.json`` lives. Revisions run only when this is set
        # AND a ``revision`` handler is given: nothing is claimed that cannot be generated.
        self.runs_root = Path(runs_root) if runs_root else None

    def _state(self, run_dir: Path, job: NightlyJob, **extra: object) -> None:
        atomic_write_json(
            run_dir / "nightly-state.json",
            {
                "schema": STATE_SCHEMA,
                "job_id": job.job_id,
                "run_id": job.run_id,
                "status": job.status,
                "updated_utc": self.now().astimezone(timezone.utc).isoformat(),
                **extra,
            },
        )

    def _cadam_config(self, entrant: NightlyEntrant) -> CadamConfig:
        return CadamConfig(
            base_url=(
                os.environ.get("CADAM_BRIDGE_BASE_URL", cadam_routes.DEFAULT_BRIDGE_CADAM_URL)
                if entrant.route == cadam_routes.ROUTE_CLI_BRIDGE
                else os.environ.get("CADAM_BASE_URL", "http://127.0.0.1:3000")
            ),
            supabase_url=os.environ.get("VITE_SUPABASE_URL", "http://127.0.0.1:54321"),
            user_id=os.environ.get("CADAM_USER_ID", ""),
            access_token=os.environ.get("CADAM_ACCESS_TOKEN", ""),
            service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            bot_email=os.environ.get("CADAM_BOT_EMAIL", ""),
            bot_password=os.environ.get("CADAM_BOT_PASSWORD", ""),
            anon_key=os.environ.get("VITE_SUPABASE_ANON_KEY", ""),
            model=entrant.model_id,
            timeout_s=entrant.timeout_s or 900,
        )

    def _run_cadam(
        self,
        job: NightlyJob,
        entrant: NightlyEntrant,
        run_dir: Path,
        registry: Mapping[str, object],
        *,
        dispatch: Mapping[str, object],
        resume_only: bool,
    ) -> float:
        spec = arena_runner.instrument_spec_from_registry(registry, job.instrument_id)
        brief = str(spec.get("task_brief") or spec.get("task_brief_short") or job.instrument_id)
        prompt = (
            f"Build a parametric OpenSCAD model of {job.instrument_id} from the attached "
            f"historical/reference image. Public brief: {brief}. Keep display-only strings "
            "separate or disable them in the fabrication export; respect the registry envelope "
            "and minimum wall. Return a complete build_parametric_model tool call."
        )
        route_provenance = self._route_provenance(entrant)
        client = CadamClient(self._cadam_config(entrant))
        generate = lambda: client.generate(  # noqa: E731 - one call, two routes
            prompt=prompt,
            reference_image=Path(job.reference_image),
            output_dir=run_dir / "cadam" / entrant.entrant_id,
            conversation_id=str(dispatch["conversation_id"]),
            resume_only=resume_only,
        )
        if entrant.route == cadam_routes.ROUTE_CLI_BRIDGE:
            config_path, key = cadam_routes.bridge_settings_for_run(os.environ)
            with cadam_routes.CliBridge(config_path, key, command=cadam_routes.bridge_command_from_env(os.environ)):
                generated = generate()  # the bridge lives only for this CADAM turn
        else:
            generated = generate()
        # The deliverable keeps the #1134 name; the adapter's own candidate.scad stays its working copy.
        named = generated.scad_path.parent / night_stem(job) / cad_names.name(night_stem(job), cad_names.MODEL_PART, "scad")
        named.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated.scad_path, named)
        arena_runner.ingest_candidate(
            run_log_path=run_dir / "run_log.json",
            registry=registry,
            instrument_id=job.instrument_id,
            model_id=entrant.entrant_id,
            scad_path=named,
            run_dir=run_dir,
            seed=job.seed,
            provenance_extra={
                "adapter_schema": "makerbench-cadam-headless-v1",
                "conversation_id": generated.conversation_id,
                "source_image_sha256": generated.image_sha256,
                "source_image": generated.prepared_image_path.as_posix(),
                "model": generated.model,
                "cost_usd": generated.cost_usd,
                **route_provenance,
            },
            # meta.route is stamped by _record_route (route_label, total) after the entrant; the label here
            # keeps the configured model id for the side-by-side smoke page
            trial_meta={"entrant_label": entrant.route_label},
        )
        _record_native(run_dir / "run_log.json", job, entrant, named)
        return generated.cost_usd

    @staticmethod
    def _route_provenance(entrant: NightlyEntrant) -> dict:
        """Which route, runtime and CLI served the turn (recorded in the candidate's provenance)."""
        info: dict = {"route": entrant.route, "entrant_label": entrant.route_label}
        if entrant.route == cadam_routes.ROUTE_CLI_BRIDGE:
            info["runtime"] = entrant.runtime
            try:
                path, _key = cadam_routes.bridge_settings_for_run(os.environ)
                settings = cadam_routes.read_bridge_config(path)
                mapped = settings.models.get(entrant.model_id, {})
                info["cli_model"] = mapped.get("cli_model")
                info["cli_version"] = cadam_routes.cli_version(settings.binary(entrant.runtime))
            except (cadam_routes.BridgeError, OSError, ValueError):
                info["cli_version"] = "unknown"
        return info

    def _run_arena(
        self,
        job: NightlyJob,
        entrant: NightlyEntrant,
        run_dir: Path,
        registry: Mapping[str, object],
    ) -> float:
        generator = providers.resolve_generator(
            entrant.model_id,
            timeout_s=entrant.timeout_s,
            backend=entrant.backend,
        )
        compiler = arena_runner.compiler_for_backend(entrant.backend)
        native_name = cad_names.name(night_stem(job), cad_names.MODEL_PART, "FCStd")
        if entrant.backend == "freecad":
            from functools import partial
            compiler = partial(compiler, native_filename=native_name)
        execute = arena_runner.make_execute_trial(
            registry=registry,
            run_dir=run_dir,
            generators={entrant.entrant_id: generator},
            compiler=compiler,
            backend=entrant.backend,
            context_tier=entrant.context_tier,
            instruments_root=self.instruments_root,
            max_turns=job.turns,
            stop_early=job.stop_early,
            max_cost_usd=entrant.max_cost_usd,
            image_paths=(
                {job.instrument_id: Path(job.reference_image)}
                if entrant.context_tier == "image"
                else None
            ),
        )
        config = OrchestrationConfig(
            instrument_ids=(job.instrument_id,),
            model_ids=(entrant.entrant_id,),
            seeds=(job.seed,),
            max_attempts=1,
            model_providers={entrant.entrant_id: providers.provider_for_model_id(entrant.model_id)},
            backend=entrant.backend,
        )
        log = run_orchestration(
            config=config,
            run_log_path=run_dir / "run_log.json",
            execute_trial=execute,
        )
        if entrant.backend == "freecad":
            log_path = run_dir / "run_log.json"
            native_log = json.loads(log_path.read_text(encoding="utf-8"))
            trial_id = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
            row = find_trial(native_log, trial_id)
            result = row.get("result") if isinstance(row, dict) else None
            artifacts = result.get("artifacts") if isinstance(result, dict) else None
            stl = artifacts.get("stl_path") if isinstance(artifacts, dict) else None
            if stl:
                native = Path(stl).parent / native_name
                if native.is_file() and not native.is_symlink() and native.stat().st_size:
                    _record_native(log_path, job, entrant, native)
        row = find_trial(log or {}, build_trial_matrix(config)[0].trial_id) or {}
        return sum((
            float(turn.get("cost_usd") or 0.0)
            for turn in ((row.get("result") or row.get("meta") or {}).get("turns") or [])
        ), 0.0)

    def _run_live(
        self,
        job: NightlyJob,
        entrant: NightlyEntrant,
        run_dir: Path,
        registry: Mapping[str, object],
    ) -> float:
        images = run_dir / "reference-images"
        images.mkdir(parents=True, exist_ok=True)
        target = images / f"{job.instrument_id}.png"
        if not target.exists():
            from PIL import Image

            with Image.open(job.reference_image) as source:
                source.convert("RGB").save(target, format="PNG", optimize=True)
        connector = "hwe-fusion" if entrant.backend == "fusion-live" else "hwe-solidworks"
        runtime, driver_model = driver_route(entrant.model_id)
        config = LiveCadConfig(
            backend=entrant.backend,
            driver_model=driver_model,
            driver_runtime=runtime,
            connector=connector,
            images_root=images,
            context_tier=entrant.context_tier,
            timeout_s=entrant.timeout_s or 1800,
            cad_stem=night_stem(job),
            env={
                key: value
                for key, value in os.environ.items()
                if key.startswith("HWE_SW_") or key.startswith("HWE_FUSION_")
            },
        )
        if not connector_available(config):
            hint = (" (start it with StudioPipeline-hwe scripts\\start_solidworks_bridge.ps1)"
                    if connector == "hwe-solidworks" else " (open Fusion with the Luthier Bridge add-in running)")
            raise LiveSeatUnavailableError(f"{connector} authenticated preflight failed{hint}")
        if connector == "hwe-solidworks":
            part_open, reason = connector_part_document(config)
            if not part_open:
                raise LiveSeatUnavailableError(reason)
        launched, message = connector_mcp_probe(config)
        if not launched:
            raise LiveSeatUnavailableError(message)
        if connector == "hwe-fusion" and connector_unattended(config) is not True:
            raise LiveSeatUnavailableError("hwe-fusion bridge is not in unattended mode: every confirm would wait for a "
                               "human (restart the Luthier Bridge add-in with LUTHIER_UNATTENDED=1; hwe#544)")
        execute = make_live_execute_trial(registry=registry, run_dir=run_dir, config=config)
        orchestration = OrchestrationConfig(
            instrument_ids=(job.instrument_id,),
            model_ids=(entrant.entrant_id,),
            seeds=(job.seed,),
            max_attempts=1,
            model_providers={entrant.entrant_id: entrant.backend},
            backend=entrant.backend,
        )
        run_orchestration(
            config=orchestration,
            run_log_path=run_dir / "run_log.json",
            execute_trial=execute,
        )
        return 0.0

    def _revision_job(
        self, jobs: list[NightlyJob], request: revisions.RevisionRequest
    ) -> NightlyJob:
        """The NightlyJob a revision request stands for: its entrant, continued from a turn.

        Seed, budget, reference image and the entrant's configuration come from the nightly job
        that produced the source run, so a revision cannot widen what that night was allowed.
        """
        assert self.runs_root is not None
        revisions.source_run_dir(self.runs_root, request.source_run_id)  # confinement only
        # A revised design's run is not in the nightly queue: follow the revision ledger back to the
        # night that produced it (chains of revisions), guarding against loops.
        origin_id, seen = request.source_run_id, set()
        original = None
        while origin_id and origin_id not in seen and len(seen) < revisions.MAX_CHAIN:
            seen.add(origin_id)
            original = next((j for j in jobs if j.run_id == origin_id), None)
            if original is not None:
                break
            origin_id = revisions.parent_run(self.runs_root, origin_id)
        if original is None:
            raise ValueError(f"source run {request.source_run_id} is not in the nightly queue or the revision ledger")
        entrant = next((e for e in original.entrants if e.entrant_id == request.entrant_id), None)
        if entrant is None:
            raise ValueError(f"{request.entrant_id} did not take part in {request.source_run_id}")
        return NightlyJob(
            job_id=request.job_id,
            instrument_id=request.instrument_id,
            reference_image=original.reference_image,
            entrants=[NightlyEntrant.from_dict(entrant.__dict__)],
            seed=original.seed,
            budget_usd=original.budget_usd,
            revision={
                **request.as_dict(),
                "source_run_dir": (self.runs_root / request.source_run_id).as_posix(),
            },
        )

    def _claim_revision(
        self, payload: Mapping[str, object], jobs: list[NightlyJob]
    ) -> Optional[tuple[NightlyJob, revisions.RevisionRequest]]:
        if self.runs_root is None or "revision" not in self.handlers:
            return None
        while True:
            request = revisions.claim_next(self.runs_root, now=self.now())
            if request is None:
                return None
            try:
                job = self._revision_job(jobs, request)
                job.validate()
            except (ValueError, OSError) as exc:  # unusable request: record it, try the next one
                revisions.finish(self.runs_root, request, ok=False, error=str(exc), now=self.now())
                continue
            return job, request

    def run(self, *, all_jobs: bool = False, max_jobs: Optional[int] = None,
            stop_on_block: bool = False) -> dict:
        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        with nightly_lease(self.lease_path, now=self.now):
            if not all_jobs and max_jobs is None:
                return self._run_one()
            results = []
            while max_jobs is None or len(results) < max_jobs:
                result = self._run_one()
                if result["status"] == "queue-empty":
                    break
                results.append(result)
                if stop_on_block and result["status"] == "blocked":
                    break
            return {"status": "blocked" if any(r["status"] == "blocked" for r in results) else "complete",
                    "jobs": results, "job_count": len(results)}

    def _run_one(self) -> dict:
        """Run one job with the caller holding the nightly lease."""
        while True:
            payload, jobs = load_queue(self.queue_path)
            job = next(
                (item for item in jobs if item.status in {"queued", "running"}),
                None,
            )
            claimed: Optional[revisions.RevisionRequest] = None
            if job is None:
                taken = self._claim_revision(payload, jobs)
                if taken is None:
                    return {"status": "queue-empty"}
                job, claimed = taken
            job.validate()
            matchup = matchup_metadata(payload)
            if not job.run_id:
                stamp = self.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                name = revisions.folder_name(job.job_id) if job.revision else job.job_id
                job.run_id = f"{stamp}-{name}"
            if job.revision:
                # the destination is derived from queue data: resolve and confine it under the
                # output root (refusing a pre-existing symlinked folder) before anything is made
                try:
                    run_dir = revisions.confined_child(self.output_root, job.run_id)
                except revisions.RevisionQueueError as exc:
                    revisions.finish(self.runs_root, claimed, ok=False, error=str(exc), now=self.now())
                    raise ValueError(str(exc)) from None
            else:
                run_dir = Path(job.run_dir) if job.run_dir else self.output_root / job.run_id
            job.run_dir = run_dir.as_posix()
            job.status = "running"
            if claimed is None:
                try:
                    save_active_job(self.queue_path, job)
                except QueueClaimChanged:
                    continue  # reread the queue and claim its current first job
            break
        run_dir.mkdir(parents=True, exist_ok=True)
        _fresh_run_log(run_dir, job, matchup=matchup)
        registry = arena_runner.load_arena_registry(self.registry_path)
        _night_inputs(run_dir, job, registry, night=self.now().astimezone(timezone.utc).date().isoformat())
        budget = _resume_budget(run_dir, limit_usd=job.budget_usd)
        outcomes: list[dict] = []
        self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)

        for entrant in job.entrants:
            previous_dispatch = budget.dispatches.get(entrant.entrant_id, {})
            if previous_dispatch.get("status") == "resolved":
                outcomes.append({"entrant_id": entrant.entrant_id, "status": "dispatch-resolved",
                                 "resolution": previous_dispatch["resolution"]})
                self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)
                continue
            if _has_trial(run_dir / "run_log.json", job, entrant):
                violation = None
                if entrant.entrant_id in budget.reservations:
                    violation = budget.charge(
                        entrant,
                        _trial_cost(run_dir / "run_log.json", job, entrant),
                    )
                outcomes.append(
                    {
                        "entrant_id": entrant.entrant_id,
                        "status": "already-complete",
                        **({"budget_violation": violation} if violation else {}),
                    }
                )
                self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)
                continue
            try:
                budget.reserve(entrant)
                dispatch = None
                resume_only = False
                if entrant.kind == "cadam" and entrant.kind not in self.handlers and not job.revision:
                    dispatch = budget.prepare_dispatch(job, entrant)
                    resume_only = str(dispatch.get("status")) == "dispatched"
                    if not resume_only:
                        dispatch["status"] = "dispatched"
                        dispatch["dispatched_utc"] = (
                            self.now().astimezone(timezone.utc).isoformat()
                        )
                # The reservation and paid-dispatch identity must be durable
                # before any provider request can leave this process.
                self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)
                handler = self.handlers.get("revision" if job.revision else entrant.kind)
                if handler is not None:
                    cost = handler(job, entrant, run_dir, registry)
                elif entrant.kind == "cadam":
                    assert dispatch is not None
                    cost = self._run_cadam(
                        job,
                        entrant,
                        run_dir,
                        registry,
                        dispatch=dispatch,
                        resume_only=resume_only,
                    )
                elif entrant.kind == "live":
                    cost = self._run_live(job, entrant, run_dir, registry)
                else:
                    cost = self._run_arena(job, entrant, run_dir, registry)
                violation = budget.charge(entrant, cost)
                if dispatch is not None:
                    dispatch["status"] = "charged"
                    dispatch["cost_usd"] = cost
                # Close the trial-row -> budget-ledger crash window before
                # moving to another entrant.
                self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)
                outcomes.append(
                    {
                        "entrant_id": entrant.entrant_id,
                        "status": "budget-overrun" if violation else "complete",
                        "cost_usd": cost,
                        **({"budget_violation": violation} if violation else {}),
                    }
                )
            except CadamStreamFailedError as exc:
                if dispatch is not None:
                    dispatch["status"] = "failed"
                _append_error_trial(run_dir / "run_log.json", job, entrant, str(exc))
                outcomes.append({"entrant_id": entrant.entrant_id, "status": "failed", "error": str(exc)})
            except CadamRecoveryRequiredError as exc:
                # Do not append a terminal trial: an operator may later
                # recover the same persisted conversation, but automatic
                # reposting is permanently forbidden.
                outcomes.append(
                    {
                        "entrant_id": entrant.entrant_id,
                        "status": "paid-dispatch-uncertain",
                        "error": str(exc),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one entrant must not kill the night.
                _append_error_trial(run_dir / "run_log.json", job, entrant, str(exc))
                outcomes.append(
                    {"entrant_id": entrant.entrant_id, "status": "error", "error": str(exc),
                     **({"seat_unavailable": True} if isinstance(exc, LiveSeatUnavailableError) else {})}
                )
            _record_route(run_dir / "run_log.json", job, entrant)
            self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)

        run_log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        arena_runner.write_json(
            run_dir / "objective_scoreline.json",
            {
                "schema": "makerbench-code-cad-objective-scoreline-v1",
                "rows": arena_runner.collect_objective_scoreline(run_log),
                **({"matchup": matchup} if matchup else {}),
            },
        )
        morning = finalize_morning_bundle(run_dir, cost_usd=budget.spent_usd)
        uncertain_dispatch = next(
            (
                row.get("error")
                for row in outcomes
                if row.get("status") == "paid-dispatch-uncertain"
            ),
            None,
        )
        seat_block = next((row["error"] for row in outcomes if row.get("seat_unavailable")), None)
        safety_block = budget.halted_reason or uncertain_dispatch or seat_block
        if safety_block:
            morning["votable"] = False
            morning["safety_block"] = safety_block
            atomic_write_json(run_dir / "morning-summary.json", morning)
        job.status = "votable" if morning["votable"] else "blocked"
        if claimed is None:
            save_active_job(self.queue_path, job)
        else:
            failure = next((row.get("error") for row in outcomes if row.get("status") in {"error", "failed", "paid-dispatch-uncertain"}), None)
            revisions.finish(
                self.runs_root, claimed, ok=failure is None, run_id=job.run_id, error=failure, now=self.now()
            )
        self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__, morning=morning)
        return {
            "status": job.status,
            "job_id": job.job_id,
            **({"matchup": matchup} if matchup else {}),
            "run_id": job.run_id,
            "run_dir": run_dir.as_posix(),
            **({"revision_of": job.revision} if job.revision else {}),
            "morning": morning,
            "outcomes": outcomes,
            "budget": budget.__dict__,
        }


def morning_summary(runs_root: Path, night: str) -> dict:
    """Read the UTC night recorded in brief.json; never rewrite run evidence."""
    if datetime.strptime(night, "%Y-%m-%d").date().isoformat() != night:
        raise ValueError("night must be YYYY-MM-DD")
    rows = []
    for run_dir in sorted(Path(runs_root).iterdir()):
        if not run_dir.is_dir() or run_dir.is_symlink() or run_dir.name.startswith("."):
            continue
        brief_path = run_dir / "brief.json"
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            brief = {}
        saved_night = brief.get("night") if isinstance(brief, dict) else None
        if saved_night != night:
            # An interrupted initial claim may have a folder but no brief yet.
            if saved_night or not re.match(r"^" + night.replace("-", "") + r"T[0-9]{6}Z-", run_dir.name):
                continue
        row = {"run_id": run_dir.name, "votable": None,
               "valid_candidate_count": None, "failed_candidate_count": None}
        try:
            summary = json.loads((run_dir / "morning-summary.json").read_text(encoding="utf-8"))
            row.update({key: summary[key] for key in
                        ("votable", "valid_candidate_count", "failed_candidate_count")})
            row["status"] = "votable" if summary["votable"] else "blocked"
            if summary.get("safety_block"):
                row["reason"] = summary["safety_block"]
        except (OSError, ValueError, KeyError, TypeError):
            row["status"] = "incomplete"
            row["reason"] = "morning summary missing or invalid"
        rows.append(row)
    return {"night": night, "runs": rows, "run_count": len(rows)}

def resolve_dispatch(*, run_id: str, entrant_id: str, outcome: str, note: str = "",
                     runs_root: Path, queue_path: Path, lease_path: Optional[Path] = None) -> dict:
    """Record an operator decision under the executor lease; never dispatch or invent cost."""
    if outcome not in {"lost", "received"}:
        raise ValueError("outcome must be lost or received")
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run must be a run ID")
    run_dir = Path(runs_root) / run_id
    if not run_dir.resolve().is_relative_to(Path(runs_root).resolve()):
        raise ValueError("run escapes runs root")
    with nightly_lease(lease_path or Path(runs_root) / ".nightly-cad.lock",
                       now=lambda: datetime.now(timezone.utc)):
        payload, jobs = load_queue(queue_path)
        job = next((job for job in jobs if job.run_id == run_id), None)
        if job is None:
            raise ValueError("run is not in the queue")
        state_path = run_dir / "nightly-state.json"
        state = json.loads(state_path.read_text())
        rows = state.get("outcomes", [])
        row = next((row for row in rows if row.get("entrant_id") == entrant_id
                    and row.get("status") == "paid-dispatch-uncertain"), None)
        if row is None:
            raise ValueError("entrant has no unresolved dispatch")
        row["status"] = "dispatch-resolved"
        row["resolution"] = {"outcome": outcome, "note": note,
                             "resolved_utc": datetime.now(timezone.utc).isoformat()}
        budget = state.get("budget", {})
        dispatch = budget.get("dispatches", {}).get(entrant_id)
        if dispatch is not None:
            dispatch["status"] = "resolved"
            dispatch["resolution"] = row["resolution"]
        # Keep reservations: a manual receipt decision is not billing evidence.
        morning = json.loads((run_dir / "morning-summary.json").read_text())
        unresolved = next((r.get("error") for r in rows
                           if r.get("status") == "paid-dispatch-uncertain"), None)
        blocker = budget.get("halted_reason") or unresolved
        # A persisted reason not represented by an entrant outcome may be an
        # independent/operator safety hold. Resolving a dispatch cannot clear
        # that hold; retain it even when budget and uncertainty are now clear.
        old_errors = {r.get("error") for r in rows}
        if morning.get("safety_block") and morning["safety_block"] not in old_errors:
            blocker = blocker or morning["safety_block"]
        if blocker:
            morning["safety_block"] = blocker
            morning["votable"] = False
        else:
            morning.pop("safety_block", None)
            morning["votable"] = (morning.get("valid_candidate_count", 0) >= 2
                                  and bool(morning.get("pair_files")))
        job.status = "votable" if morning["votable"] else "blocked"
        state.update(status=job.status, morning=morning)
        atomic_write_json(state_path, state)
        atomic_write_json(run_dir / "morning-summary.json", morning)
        save_queue(queue_path, payload, jobs)
        return state

