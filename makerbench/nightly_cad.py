"""Resumable, budgeted overnight orchestration for the Code-CAD Arena."""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import socket
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

from . import code_cad_arena_runner as arena_runner
from . import code_cad_providers as providers
from .cadam_adapter import CadamClient, CadamConfig, CadamRecoveryRequiredError
from .code_cad_orchestrator import OrchestrationConfig, run_orchestration
from .live_cad_runner import LiveCadConfig, connector_available, make_live_execute_trial
from .run_log_io import atomic_write_json, file_lock


SCHEMA = "makerbench-nightly-cad-queue-v1"
STATE_SCHEMA = "makerbench-nightly-cad-state-v1"
MORNING_SCHEMA = "makerbench-nightly-cad-morning-v1"


@dataclass(frozen=True)
class NightlyEntrant:
    entrant_id: str
    kind: str
    model_id: str
    backend: str = "openscad"
    context_tier: str = "blind"
    max_cost_usd: float = 0.0
    timeout_s: Optional[int] = None

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
        )

    def validate(self) -> None:
        if not self.entrant_id or not self.model_id:
            raise ValueError("nightly entrants require entrant_id and model_id")
        if self.kind not in {"arena", "cadam", "live"}:
            raise ValueError(f"unsupported nightly entrant kind: {self.kind}")
        if self.kind == "live" and self.backend not in {"solidworks-live", "fusion-live"}:
            raise ValueError("live entrants require solidworks-live or fusion-live backend")
        if self.max_cost_usd < 0:
            raise ValueError("max_cost_usd cannot be negative")


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
        )

    def validate(self) -> None:
        if not self.job_id or not self.instrument_id:
            raise ValueError("nightly jobs require job_id and instrument_id")
        if not Path(self.reference_image).is_file():
            raise ValueError(f"nightly reference image is missing: {self.reference_image}")
        if len(self.entrants) < 2:
            raise ValueError("nightly jobs require at least two entrants")
        if self.budget_usd < 0:
            raise ValueError("budget_usd cannot be negative")
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


def _fresh_run_log(run_dir: Path, job: NightlyJob) -> None:
    path = run_dir / "run_log.json"
    if path.exists():
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
            },
            "trials": [],
            "summary": {"counts": {}, "total_trials": 0, "total_attempts": 0},
        },
    )


def _has_trial(run_log_path: Path, job: NightlyJob, entrant: NightlyEntrant) -> bool:
    payload = json.loads(run_log_path.read_text(encoding="utf-8"))
    expected = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    return any(row.get("trial_id") == expected for row in payload.get("trials") or [])


def _trial_cost(run_log_path: Path, job: NightlyJob, entrant: NightlyEntrant) -> float:
    """Recover a completed entrant's durable cost from its provenance record."""

    payload = json.loads(run_log_path.read_text(encoding="utf-8"))
    expected = f"{job.instrument_id}__seed{job.seed}__rep0__{entrant.entrant_id}"
    row = next(
        (item for item in payload.get("trials") or [] if item.get("trial_id") == expected),
        None,
    )
    if not isinstance(row, Mapping):
        return 0.0
    result = row.get("result")
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
        if any(row.get("trial_id") == trial_id for row in payload.get("trials") or []):
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
        for row in payload["trials"]:
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
    rows_by_id = {str(row.get("trial_id")): row for row in run_log.get("trials") or []}
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
        for row in run_log.get("trials") or []
        if row.get("trial_id") not in valid_ids
    ]
    summary = {
        "schema": MORNING_SCHEMA,
        "run_id": run_dir.name,
        "votable": len(valid_ids) >= 2 and bool(pair_files),
        "valid_candidate_count": len(valid_ids),
        "failed_candidate_count": len(failures),
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
            base_url=os.environ.get("CADAM_BASE_URL", "http://127.0.0.1:3000"),
            supabase_url=os.environ.get("VITE_SUPABASE_URL", "http://127.0.0.1:54321"),
            user_id=os.environ.get("CADAM_USER_ID", ""),
            access_token=os.environ.get("CADAM_ACCESS_TOKEN", ""),
            service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
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
        generated = CadamClient(self._cadam_config(entrant)).generate(
            prompt=prompt,
            reference_image=Path(job.reference_image),
            output_dir=run_dir / "cadam" / entrant.entrant_id,
            conversation_id=str(dispatch["conversation_id"]),
            resume_only=resume_only,
        )
        arena_runner.ingest_candidate(
            run_log_path=run_dir / "run_log.json",
            registry=registry,
            instrument_id=job.instrument_id,
            model_id=entrant.entrant_id,
            scad_path=generated.scad_path,
            run_dir=run_dir,
            seed=job.seed,
            provenance_extra={
                "adapter_schema": "makerbench-cadam-headless-v1",
                "conversation_id": generated.conversation_id,
                "source_image_sha256": generated.image_sha256,
                "source_image": generated.prepared_image_path.as_posix(),
                "model": generated.model,
                "cost_usd": generated.cost_usd,
            },
        )
        return generated.cost_usd

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
        execute = arena_runner.make_execute_trial(
            registry=registry,
            run_dir=run_dir,
            generators={entrant.entrant_id: generator},
            compiler=arena_runner.compiler_for_backend(entrant.backend),
            context_tier=entrant.context_tier,
            instruments_root=self.instruments_root,
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
        run_orchestration(
            config=config,
            run_log_path=run_dir / "run_log.json",
            execute_trial=execute,
        )
        return 0.0

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
        config = LiveCadConfig(
            backend=entrant.backend,
            driver_model=entrant.model_id,
            connector=connector,
            images_root=images,
            context_tier=entrant.context_tier,
            timeout_s=entrant.timeout_s or 1800,
            env={
                key: value
                for key, value in os.environ.items()
                if key.startswith("HWE_SW_") or key.startswith("HWE_FUSION_")
            },
        )
        if not connector_available(config):
            raise RuntimeError(f"{connector} authenticated preflight failed")
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

    def run(self) -> dict:
        with nightly_lease(self.lease_path, now=self.now):
            payload, jobs = load_queue(self.queue_path)
            job = next(
                (item for item in jobs if item.status in {"queued", "running"}),
                None,
            )
            if job is None:
                return {"status": "queue-empty"}
            job.validate()
            if not job.run_id:
                stamp = self.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                job.run_id = f"{stamp}-{job.job_id}"
            run_dir = Path(job.run_dir) if job.run_dir else self.output_root / job.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            job.run_dir = run_dir.as_posix()
            job.status = "running"
            save_queue(self.queue_path, payload, jobs)
            _fresh_run_log(run_dir, job)
            registry = arena_runner.load_arena_registry(self.registry_path)
            budget = _resume_budget(run_dir, limit_usd=job.budget_usd)
            outcomes: list[dict] = []
            self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)

            for entrant in job.entrants:
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
                    if entrant.kind == "cadam" and entrant.kind not in self.handlers:
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
                    handler = self.handlers.get(entrant.kind)
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
                        {"entrant_id": entrant.entrant_id, "status": "error", "error": str(exc)}
                    )
                self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__)

            run_log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
            arena_runner.write_json(
                run_dir / "objective_scoreline.json",
                {
                    "schema": "makerbench-code-cad-objective-scoreline-v1",
                    "rows": arena_runner.collect_objective_scoreline(run_log),
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
            safety_block = budget.halted_reason or uncertain_dispatch
            if safety_block:
                morning["votable"] = False
                morning["safety_block"] = safety_block
                atomic_write_json(run_dir / "morning-summary.json", morning)
            job.status = "votable" if morning["votable"] else "blocked"
            save_queue(self.queue_path, payload, jobs)
            self._state(run_dir, job, outcomes=outcomes, budget=budget.__dict__, morning=morning)
            return {
                "status": job.status,
                "job_id": job.job_id,
                "run_id": job.run_id,
                "run_dir": run_dir.as_posix(),
                "morning": morning,
                "outcomes": outcomes,
                "budget": budget.__dict__,
            }
