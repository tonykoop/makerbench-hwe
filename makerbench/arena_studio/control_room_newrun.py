"""Control Room: New Run launcher (#1104 / #1114).

Turns the New Run form into nightly-queue jobs (``makerbench-nightly-cad-queue-v1``,
the same ``NightlyJob``/``NightlyEntrant`` the runner reads) plus a Control Room
*plan* per job holding what the queue schema has no field for: the deliverable mode,
variant, brief, turns and the stop-early switch. The runner rewrites the queue
through ``NightlyJob.as_dict()``, which keeps only its own fields, so the plan lives
beside the queue (``runs/control-room/plans/<job_id>.json``), keyed by ``job_id``.

Nothing here launches a model. "Add to tonight" appends to the real nightly queue
only when the Studio was started with ``--control-room-queue-writes``; otherwise it
writes a draft next to the plans and says exactly what would have been queued.
Estimates come from ``doe.estimate_model_cost_and_time`` (telemetry, never invented).
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .. import nightly_cad
from ..run_log_io import atomic_write_json, file_lock
from . import doe

PLAN_SCHEMA = "makerbench-control-room-run-plan-v1"
DRAFT_SCHEMA = "makerbench-control-room-tonight-draft-v1"
CONTROL_ROOM_DIR = Path("control-room")
MAX_TURNS = 8
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_REFERENCES = 6
IMAGE_MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp"}
DELIVERABLES = ("single", "assembly")
SEAT_TOOLS = frozenset({"solidworks", "fusion"})


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    family: str  # whose subscription quota a CLI route spends: "claude" or "codex"
    openrouter: str  # the OpenRouter slug CADAM sends
    cli: Optional[str]  # the subscription CLI model id, None when only on OpenRouter


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    detail: str
    route: str  # "openrouter" or "subscription"
    kind: str  # NightlyEntrant.kind
    backend: str  # NightlyEntrant.backend
    native: str
    blocked: Optional[str] = None  # why the whole row is unavailable


MODELS: tuple[Model, ...] = (
    Model("fable", "Fable 5.1", "claude", "anthropic/claude-fable-5.1", None),
    Model("opus", "Opus 5.5", "claude", "anthropic/claude-opus-5.5", "claude-code-opus-5.5"),
    Model("sonnet", "Sonnet 5.5", "claude", "anthropic/claude-sonnet-5.5", "claude-code-sonnet-5.5"),
    Model("sol", "GPT-6.1 Sol", "codex", "openai/gpt-6.1-sol", "codex-gpt-6.1-sol"),
)

TOOLS: tuple[Tool, ...] = (
    Tool("cadam", "CADAM", "OpenRouter · OpenSCAD", "openrouter", "cadam", "openscad", ".scad"),
    Tool("cadam-cli", "CADAM via CLI", "your subscription", "subscription", "cadam", "openscad", ".scad",
         blocked="The CADAM CLI route (#1099) is not built yet."),
    Tool("freecad", "FreeCAD", "editable .FCStd", "subscription", "arena", "freecad", ".FCStd"),
    Tool("solidworks", "SolidWorks", "live bridge · .SLDPRT / .SLDASM", "subscription", "live", "solidworks-live", ".SLDPRT"),
    Tool("fusion", "Fusion", "live bridge · .f3d", "subscription", "live", "fusion-live", ".f3d"),
)

_MODELS = {model.id: model for model in MODELS}
_TOOLS = {tool.id: tool for tool in TOOLS}
_SLUG = re.compile(r"[^a-z0-9]+")


class NewRunError(ValueError):
    """A New Run form that cannot become a job as sent."""


def cell_status(tool: Tool, model: Model) -> Optional[str]:
    """Why a tool × model cell cannot run, or None when it can."""
    if tool.blocked:
        return tool.blocked
    if tool.route == "subscription" and model.cli is None:
        return f"{model.name} is only reachable through OpenRouter."
    return None


def entrant_for(tool: Tool, model: Model) -> nightly_cad.NightlyEntrant:
    model_id = model.openrouter if tool.route == "openrouter" else model.cli
    return nightly_cad.NightlyEntrant(
        entrant_id=f"{tool.id}-{model.id}",
        kind=tool.kind,
        model_id=str(model_id),
        backend=tool.backend,
    )


def catalog(instruments: Sequence[Mapping[str, Any]], availability: Optional[Callable[[str], Mapping[str, Any]]] = None) -> dict:
    """What the form offers: instruments, the entrant grid and its unavailable cells."""
    availability = availability or doe.backend_availability
    backends = {}
    for tool in TOOLS:
        try:
            backends[tool.backend] = dict(availability(tool.backend)) if tool.backend in doe.BACKENDS else {
                "status": "requires_preflight", "reason": "Checked when the run starts"}
        except doe.DoeValidationError:
            backends[tool.backend] = {"status": "requires_preflight", "reason": "Checked when the run starts"}
    return {
        "instruments": [
            {
                "id": str(item.get("id")),
                "name": str(item.get("display_name") or item.get("id")),
                "family": item.get("family"),
                "assembly": bool(item.get("assembly")),
                "envelope_mm": item.get("envelope_mm"),
                "brief": item.get("task_brief") or "",
            }
            for item in instruments
            if item.get("id")
        ],
        "models": [{"id": m.id, "name": m.name, "family": m.family} for m in MODELS],
        "tools": [
            {
                "id": t.id, "name": t.name, "detail": t.detail, "route": t.route, "seat": t.id in SEAT_TOOLS,
                "backend": backends.get(t.backend),
                "cells": {m.id: cell_status(t, m) for m in MODELS},
            }
            for t in TOOLS
        ],
        "max_turns": MAX_TURNS,
    }


def parse_entrants(form: Mapping[str, Any]) -> list[tuple[Tool, Model]]:
    picks = form.get("entrants")
    if not isinstance(picks, list):
        raise NewRunError("Pick entrants as tool:model pairs.")
    out: list[tuple[Tool, Model]] = []
    for pick in dict.fromkeys(map(str, picks)):
        tool_id, _, model_id = pick.partition(":")
        tool, model = _TOOLS.get(tool_id), _MODELS.get(model_id)
        if tool is None or model is None:
            raise NewRunError(f"Unknown entrant: {pick}")
        reason = cell_status(tool, model)
        if reason:
            raise NewRunError(f"{tool.name} with {model.name} cannot run: {reason}")
        out.append((tool, model))
    return out


def estimate(form: Mapping[str, Any], *, telemetry_store: str = doe.DEFAULT_TELEMETRY_STORE,
             estimator: Optional[Callable[..., Mapping[str, Any]]] = None) -> dict:
    """Cost, time and subscription use for a form, per entrant and in total.

    One estimator sample stands for one design (a whole trial). Unknown stays
    unknown: a total with any unknown paid entrant is reported as a lower bound.
    """
    estimator = estimator or doe.estimate_model_cost_and_time
    picks = parse_entrants(form)
    variants = max(1, len(form.get("variants") or []) or 1)
    rows = []
    for tool, model in picks:
        entrant = entrant_for(tool, model)
        if tool.route == "subscription":
            cost: Optional[float] = 0.0
            sample = estimator(entrant.model_id, telemetry_store=telemetry_store, backend=entrant.backend)
            source = "subscription"
        else:
            sample = estimator(entrant.model_id, telemetry_store=telemetry_store, backend=entrant.backend)
            cost = sample.get("cost_usd")
            source = sample.get("cost_source") or "unknown"
        duration = sample.get("duration_s")
        rows.append({
            "entrant": f"{tool.id}:{model.id}",
            "tool": tool.name,
            "model": model.name,
            "route": tool.route,
            "quota_family": model.family if tool.route == "subscription" else None,
            "designs": variants,
            "cost_usd": round(cost * variants, 4) if isinstance(cost, (int, float)) else None,
            "cost_source": source,
            "duration_s": round(duration * variants, 1) if isinstance(duration, (int, float)) else None,
            "seat": tool.id in SEAT_TOOLS,
        })
    paid = [row for row in rows if row["route"] == "openrouter"]
    known_cost = round(sum(row["cost_usd"] or 0 for row in paid), 4)
    seat_s = [row["duration_s"] for row in rows if row["seat"]]
    free_s = [row["duration_s"] for row in rows if not row["seat"]]
    # Seat tools share one CAD seat and run one after another; the rest overlap.
    if all(isinstance(s, (int, float)) for s in seat_s + free_s) and rows:
        wall = max(sum(seat_s), max(free_s, default=0.0))
    else:
        wall = None
    return {
        "designs": len(rows) * variants,
        "entrants": rows,
        "openrouter_usd": known_cost,
        "openrouter_complete": all(row["cost_usd"] is not None for row in paid),
        "unknown_cost": [row["entrant"] for row in paid if row["cost_usd"] is None],
        "subscription_designs": {
            family: sum(row["designs"] for row in rows if row["quota_family"] == family) for family in ("claude", "codex")
        },
        "wall_time_s": wall,
        "cap_usd": form.get("cap_usd"),
        "over_cap": isinstance(form.get("cap_usd"), (int, float)) and known_cost > float(form["cap_usd"]),
    }


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.lower()).strip("-")[:40] or "run"


def _save_references(images: Sequence[Mapping[str, Any]], target: Path) -> list[Path]:
    saved = []
    for index, image in enumerate(images[:MAX_REFERENCES]):
        data_url = str(image.get("data") or "")
        head, _, body = data_url.partition(",")
        if not head.startswith("data:image/") or ";base64" not in head:
            raise NewRunError("Reference images must be PNG, JPEG or WebP files.")
        raw = base64.b64decode(body, validate=True)
        if len(raw) > MAX_REFERENCE_BYTES:
            raise NewRunError("A reference image is limited to 10 MB.")
        suffix = next((ext for magic, ext in IMAGE_MAGIC.items() if raw.startswith(magic)), None)
        if suffix is None:
            raise NewRunError("Reference images must be PNG, JPEG or WebP files.")
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"reference-{index + 1}{suffix}"
        path.write_bytes(raw)
        saved.append(path)
    return saved


def build_jobs(form: Mapping[str, Any], *, instruments: Mapping[str, Mapping[str, Any]], runs_root: Path,
               task_reference: Callable[[str], Optional[Path]], now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Validate the form and return ``[{"job": NightlyJob dict, "plan": plan}]``, one per variant.

    Uploaded reference images are written under the plan folder (needed for the job's
    ``reference_image``); nothing touches the queue here.
    """
    instrument_id = str(form.get("instrument") or "")
    if instrument_id not in instruments:
        raise NewRunError("Choose an instrument.")
    deliverable = form.get("deliverable") or "single"
    if deliverable not in DELIVERABLES:
        raise NewRunError("Deliverable must be a single part or a wood assembly.")
    picks = parse_entrants(form)
    if len(picks) < 2:
        raise NewRunError("The nightly runner needs at least two entrants per night.")
    turns = form.get("turns")
    if not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= MAX_TURNS:
        raise NewRunError(f"Turns must be a whole number from 1 to {MAX_TURNS}.")
    cap = form.get("cap_usd", 0)
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or not 0 <= float(cap) <= 100:
        raise NewRunError("The OpenRouter cap must be between $0 and $100.")
    brief = str(form.get("brief") or "").strip()
    if not brief:
        raise NewRunError("Write a brief for the entrants.")
    if len(brief) > 8000:
        raise NewRunError("The brief is limited to 8000 characters.")
    variants = [str(v) for v in form.get("variants") or []] or [""]
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base_id = f"cr-{stamp}-{_slug(instrument_id)}"
    plans_root = runs_root / CONTROL_ROOM_DIR / "plans"
    uploads = _save_references(form.get("references") or [], plans_root / base_id / "references")
    reference = uploads[0] if uploads else task_reference(instrument_id)
    if reference is None:
        raise NewRunError("Add a reference image: this instrument has none on disk.")
    paid = [tool for tool, _ in picks if tool.route == "openrouter"]
    per_paid = round(float(cap) / len(paid), 4) if paid else 0.0
    out = []
    for variant in variants:
        job_id = base_id + (f"-{_slug(variant)}" if variant else "")
        entrants = [
            nightly_cad.NightlyEntrant(**{**entrant_for(tool, model).__dict__,
                                          "max_cost_usd": per_paid if tool.route == "openrouter" else 0.0})
            for tool, model in picks
        ]
        job = nightly_cad.NightlyJob(job_id=job_id, instrument_id=instrument_id, reference_image=str(reference),
                                     entrants=entrants, budget_usd=float(cap))
        job.validate()
        plan = {
            "schema": PLAN_SCHEMA,
            "job_id": job_id,
            "instrument_id": instrument_id,
            "variant": variant or None,
            "deliverable": deliverable,
            "brief": brief,
            "max_turns": turns,
            "stop_when_all_checks_pass": bool(form.get("stop_early", True)),
            "reference_images": [path.name for path in uploads] or [Path(str(reference)).name],
            "entrants": [f"{tool.id}:{model.id}" for tool, model in picks],
            "openrouter_cap_usd": float(cap),
            "created_utc": (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat(),
        }
        out.append({"job": job.as_dict(), "plan": plan})
    return out


def write_plans(runs_root: Path, built: Sequence[Mapping[str, Any]]) -> list[str]:
    plans = runs_root / CONTROL_ROOM_DIR / "plans"
    names = []
    for item in built:
        path = plans / f"{item['plan']['job_id']}.json"
        atomic_write_json(path, item["plan"])
        names.append(path.relative_to(runs_root).as_posix())
    return names


def write_draft(runs_root: Path, built: Sequence[Mapping[str, Any]]) -> str:
    """Queue writes are off: keep what would have been queued, for the manager to see."""
    path = runs_root / CONTROL_ROOM_DIR / "drafts" / f"{built[0]['plan']['job_id']}.json"
    atomic_write_json(path, {"schema": DRAFT_SCHEMA, "queue_schema": nightly_cad.SCHEMA,
                             "jobs": [item["job"] for item in built]})
    return path.relative_to(runs_root).as_posix()


def append_jobs(queue_path: Path, built: Sequence[Mapping[str, Any]]) -> int:
    """Append jobs to the nightly queue under its lock, through the runner's own load/save."""
    with file_lock(queue_path):
        if queue_path.is_file():
            payload, jobs = nightly_cad.load_queue(queue_path)
        else:
            payload, jobs = {"schema": nightly_cad.SCHEMA}, []
        known = {job.job_id for job in jobs}
        added = [nightly_cad.NightlyJob.from_dict(item["job"]) for item in built if item["job"]["job_id"] not in known]
        nightly_cad.save_queue(queue_path, payload, jobs + added)
    return len(added)


def ready_lights(*, queue_writes: bool, queue_path: Path, environ: Mapping[str, str] = os.environ) -> list[dict[str, str]]:
    """Presence-only status lights. Secrets are never read, only checked for presence."""
    lights = [
        {"state": "ok" if environ.get("OPENROUTER_API_KEY") else "warn",
         "text": "OpenRouter key present" if environ.get("OPENROUTER_API_KEY") else "No OpenRouter key in this server's environment"},
        {"state": "ok" if queue_path.is_file() else "warn",
         "text": "Nightly queue file found" if queue_path.is_file() else "No nightly queue file yet; one is created on the first add"},
        {"state": "ok" if queue_writes else "warn",
         "text": "Add to tonight writes the nightly queue" if queue_writes
         else "Queue writes are off: Add to tonight saves a draft only"},
        {"state": "warn", "text": "Nightly runs need your GO (#664); this screen never switches them on"},
    ]
    return lights
