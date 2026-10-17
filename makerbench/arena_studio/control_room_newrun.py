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


def registry_instruments(registry: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Arena registry tasks as New Run cards (the fallback when there is no library snapshot)."""
    return [
        {
            "id": str(item["id"]), "name": str(item.get("display_name") or item["id"]), "family": item.get("family"),
            "subtype": "", "variants": 1, "hero": "", "cad": "", "completeness": "", "wolfram": "",
            "arena_task": str(item["id"]), "assembly": bool(item.get("assembly")),
            "envelope_mm": item.get("envelope_mm"), "brief": item.get("task_brief") or "",
        }
        for item in registry
        if item.get("id")
    ]


def library_instruments(entries: Sequence[Mapping[str, Any]], registry: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Library cards, each linked to its arena task when the registry has one (#1107).

    A linked card uses the task id, so its job is graded and its reference image found;
    an unlinked one keeps the library slug and needs an uploaded reference image.
    """
    from .library_manifest import registry_links

    links = registry_links(registry)
    tasks = {str(task.get("id")): task for task in registry}
    out = []
    for entry in entries:
        task_id = links.get(entry["slug"])
        task = tasks.get(task_id or "", {})
        out.append({
            "id": task_id or entry["slug"], "slug": entry["slug"], "name": entry["title"],
            "family": entry["family"], "family_label": entry.get("family_label"), "subtype": entry["subtype"],
            "variants": entry["variants"], "hero": entry["hero"], "cad": entry["cad"],
            "completeness": entry["completeness"], "wolfram": entry["wolfram"], "arena_task": task_id,
            "assembly": bool(task.get("assembly")), "envelope_mm": task.get("envelope_mm"),
            "brief": task.get("task_brief") or "",
        })
    return out


def catalog(instruments: Sequence[Mapping[str, Any]], availability: Optional[Callable[[str], Mapping[str, Any]]] = None) -> dict:
    """What the form offers: instruments (already shaped as cards), the entrant grid and its unavailable cells."""
    availability = availability or doe.backend_availability
    backends = {}
    for tool in TOOLS:
        try:
            backends[tool.backend] = dict(availability(tool.backend)) if tool.backend in doe.BACKENDS else {
                "status": "requires_preflight", "reason": "Checked when the run starts"}
        except doe.DoeValidationError:
            backends[tool.backend] = {"status": "requires_preflight", "reason": "Checked when the run starts"}
    return {
        "instruments": list(instruments),
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


MAX_VARIANTS = 12
#: What the nightly runner executes today (nightly_cad): the registry brief, one attempt per
#: entrant, one reference image, the instrument as registered. Anything else is staged.
RUNNER_TURNS = 1


class UnsafePath(NewRunError):
    """A Control Room write destination that is a symlink or leaves runs/."""


def confined(runs_root: Path, *parts: str) -> Path:
    """``runs_root/<parts>``, refusing a symlink on any component (``runs/`` included) and any escape.

    Checked before anything is created; parents are created only after the check.
    """
    root = Path(runs_root)
    path = root
    for part in ("", *parts):
        path = path / part if part else path
        if path.is_symlink():
            raise UnsafePath(f"Refusing to write through a symlink ({path.name or 'runs'}).")
    resolved_root = root.resolve()
    if path.parent.exists() and not path.parent.resolve().is_relative_to(resolved_root):
        raise UnsafePath("A Control Room write destination resolves outside runs/.")
    return path


def _save_references(images: Sequence[Mapping[str, Any]], runs_root: Path, base_id: str) -> list[Path]:
    saved = []
    for index, image in enumerate(images[:MAX_REFERENCES]):
        data_url = str(image.get("data") or "")
        head, _, body = data_url.partition(",")
        if not head.startswith("data:image/") or ";base64" not in head:
            raise NewRunError("Reference images must be PNG, JPEG or WebP files.")
        try:
            raw = base64.b64decode(body, validate=True)
        except ValueError:
            raise NewRunError("Reference images must be PNG, JPEG or WebP files.") from None
        if len(raw) > MAX_REFERENCE_BYTES:
            raise NewRunError("A reference image is limited to 10 MB.")
        suffix = next((ext for magic, ext in IMAGE_MAGIC.items() if raw.startswith(magic)), None)
        if suffix is None:
            raise NewRunError("Reference images must be PNG, JPEG or WebP files.")
        name = f"reference-{index + 1}{suffix}"
        path = confined(runs_root, str(CONTROL_ROOM_DIR), "plans", base_id, "references", name)
        path.parent.mkdir(parents=True, exist_ok=True)
        confined(runs_root, str(CONTROL_ROOM_DIR), "plans", base_id, "references", name)
        path.write_bytes(raw)
        saved.append(path)
    return saved


def validate_form(form: Mapping[str, Any], instruments: Mapping[str, Mapping[str, Any]], *,
                  for_estimate: bool = False) -> dict[str, Any]:
    """The one validator for estimate, Add to tonight and Run now; never touches disk.

    An estimate needs only valid entrants, turns, cap and variants; the other routes
    also need a known instrument, two entrants and a brief.
    """
    picks = parse_entrants(form)
    turns = form.get("turns")
    if not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= MAX_TURNS:
        raise NewRunError(f"Turns must be a whole number from 1 to {MAX_TURNS}.")
    cap = form.get("cap_usd", 0)
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or not 0 <= float(cap) <= 100:
        raise NewRunError("The OpenRouter cap must be between $0 and $100.")
    variants = form.get("variants") or []
    if not isinstance(variants, list) or len(variants) > MAX_VARIANTS or any(
            not isinstance(v, str) or not v.strip() or len(v) > 80 for v in variants):
        raise NewRunError(f"Variants must be up to {MAX_VARIANTS} short names.")
    deliverable = form.get("deliverable") or "single"
    if deliverable not in DELIVERABLES:
        raise NewRunError("Deliverable must be a single part or a wood assembly.")
    instrument_id = str(form.get("instrument") or "")
    brief = str(form.get("brief") or "").strip()
    if not for_estimate:
        if instrument_id not in instruments:
            raise NewRunError("Choose an instrument.")
        if len(picks) < 2:
            raise NewRunError("The nightly runner needs at least two entrants per night.")
        if not brief:
            raise NewRunError("Write a brief for the entrants.")
        if len(brief) > 8000:
            raise NewRunError("The brief is limited to 8000 characters.")
    elif instrument_id and instrument_id not in instruments:
        raise NewRunError("Choose an instrument.")
    return {"picks": picks, "turns": turns, "cap": float(cap), "variants": [v.strip() for v in variants],
            "deliverable": deliverable, "instrument_id": instrument_id, "brief": brief}


def runner_gaps(form: Mapping[str, Any], card: Optional[Mapping[str, Any]]) -> list[str]:
    """Settings in this form the nightly runner would silently drop today (it reads no plans)."""
    gaps = []
    registry_brief = str((card or {}).get("brief") or (card or {}).get("task_brief") or "").strip()
    if card is not None and str(form.get("brief") or "").strip() != registry_brief:
        gaps.append("your edited brief (the runner sends the registry brief)")
    if form.get("variants"):
        gaps.append("variants (the runner builds the instrument as registered)")
    if (form.get("deliverable") or "single") == "assembly":
        gaps.append("the wood-assembly mode")
    if isinstance(form.get("turns"), int) and form["turns"] != RUNNER_TURNS:
        gaps.append(f"{form['turns']} turns (the runner gives each entrant one attempt)")
    if len(form.get("references") or []) > 1:
        gaps.append("extra reference images (the runner uses the first)")
    return gaps


RUN_NOW_REFUSAL = "Run now uses the default brief and one variant; use Add to tonight for custom settings."
LIVE_CAD_REFUSAL = "Live CAD runs are scheduled through Add to tonight (one CAD seat, supervised)"

#: What ``ArenaStudioService.launch_competition`` actually executes, field by field of the
#: New Run form. Its ``arena run`` command carries the instrument, the models, the backend
#: and the task's reference image, and hard-codes ``--max-attempts 1 --reps 1`` with no
#: budget or schedule argument. Every form field must appear here (a test enforces it), so a
#: new setting is refused by Run now until the launcher really carries it.
RUN_NOW_FIELDS: dict[str, str] = {
    "instrument": "carried",
    "entrants": "carried",            # subscription, non-live routes only: CADAM and SolidWorks/Fusion refused
    "turns": "one attempt",           # --max-attempts 1
    "brief": "registry brief",        # the arena reads the registry brief
    "variants": "none",               # the instrument as registered
    "deliverable": "single part",     # no assembly mode in arena run
    "references": "task reference",   # --image-map uses the task's own image
    "cap_usd": "no spend",            # no budget argument; safe only because no paid route can run
    "stop_early": "one attempt",      # nothing to stop early with a single attempt
}


def run_now_gaps(form: Mapping[str, Any], card: Optional[Mapping[str, Any]]) -> list[str]:
    """Settings in this form that Run now would not execute (see RUN_NOW_FIELDS)."""
    gaps = []
    registry_brief = str((card or {}).get("brief") or (card or {}).get("task_brief") or "").strip()
    if card is not None and str(form.get("brief") or "").strip() != registry_brief:
        gaps.append("your edited brief (Run now sends the registry brief)")
    if form.get("variants"):
        gaps.append("variants (Run now builds the instrument as registered)")
    if (form.get("deliverable") or "single") != "single":
        gaps.append("the wood-assembly mode")
    if form.get("turns", RUNNER_TURNS) != RUNNER_TURNS:
        gaps.append(f"{form.get('turns')} turns (Run now gives each entrant one attempt)")
    if form.get("references"):
        gaps.append("reference images you added (Run now uses the task's own)")
    paid = [pick for pick in form.get("entrants") or [] if _TOOLS.get(str(pick).split(":")[0]) is not None
            and _TOOLS[str(pick).split(":")[0]].route == "openrouter"]
    if paid:
        gaps.append("paid OpenRouter entrants (Run now has no spending cap)")
    # arena run passes no --driver-model, so a live backend would drive every model with its default.
    if any(_TOOLS.get(str(pick).split(":")[0]) is not None and _TOOLS[str(pick).split(":")[0]].kind == "live"
           for pick in form.get("entrants") or []):
        gaps.append(LIVE_CAD_REFUSAL)
    return gaps


def build_jobs(form: Mapping[str, Any], *, instruments: Mapping[str, Mapping[str, Any]], runs_root: Path,
               task_reference: Callable[[str], Optional[Path]], now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Validate the form and return ``[{"job": NightlyJob dict, "plan": plan}]``, one per variant.

    The cap bounds the whole sweep: each variant's job gets an equal share, split again
    across its paid entrants. Uploaded reference images are written (confined) under
    the plan folder; nothing touches the queue here.
    """
    checked = validate_form(form, instruments)
    picks, cap, instrument_id = checked["picks"], checked["cap"], checked["instrument_id"]
    variants = checked["variants"] or [""]
    when = now or datetime.now(timezone.utc)
    base_id = f"cr-{when.strftime('%Y%m%dT%H%M%SZ')}-{_slug(instrument_id)}"
    plans_dir = confined(runs_root, str(CONTROL_ROOM_DIR), "plans")
    suffix = 2
    while (plans_dir / base_id).exists() or any((plans_dir / f"{base_id}{('-' + _slug(v)) if v else ''}.json").exists() for v in variants):
        base_id = f"cr-{when.strftime('%Y%m%dT%H%M%SZ')}-{_slug(instrument_id)}-{suffix}"  # same second, same instrument
        suffix += 1
    uploads = _save_references(form.get("references") or [], runs_root, base_id)
    reference = uploads[0] if uploads else task_reference(instrument_id)
    if reference is None:
        raise NewRunError("Add a reference image: this instrument has none on disk.")
    per_job = round(cap / len(variants), 4)
    paid = [tool for tool, _ in picks if tool.route == "openrouter"]
    per_paid = round(per_job / len(paid), 4) if paid else 0.0
    gaps = runner_gaps(form, instruments.get(instrument_id))
    out = []
    for variant in variants:
        job_id = base_id + (f"-{_slug(variant)}" if variant else "")
        entrants = [
            nightly_cad.NightlyEntrant(**{**entrant_for(tool, model).__dict__,
                                          "max_cost_usd": per_paid if tool.route == "openrouter" else 0.0})
            for tool, model in picks
        ]
        job = nightly_cad.NightlyJob(job_id=job_id, instrument_id=instrument_id, reference_image=str(reference),
                                     entrants=entrants, budget_usd=per_job)
        job.validate()
        plan = {
            "schema": PLAN_SCHEMA,
            "job_id": job_id,
            "instrument_id": instrument_id,
            "variant": variant or None,
            "deliverable": checked["deliverable"],
            "brief": checked["brief"],
            "max_turns": checked["turns"],
            "stop_when_all_checks_pass": bool(form.get("stop_early", True)),
            "reference_images": [path.name for path in uploads] or [Path(str(reference)).name],
            "entrants": [f"{tool.id}:{model.id}" for tool, model in picks],
            "openrouter_cap_usd": cap,
            "job_budget_usd": per_job,
            "runner_cannot_apply": gaps,
            "created_utc": when.replace(microsecond=0).isoformat(),
        }
        out.append({"job": job.as_dict(), "plan": plan})
    return out


def write_plans(runs_root: Path, built: Sequence[Mapping[str, Any]]) -> list[str]:
    names = []
    for item in built:
        path = confined(runs_root, str(CONTROL_ROOM_DIR), "plans", f"{item['plan']['job_id']}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(confined(runs_root, str(CONTROL_ROOM_DIR), "plans", path.name), item["plan"])
        names.append(path.relative_to(runs_root).as_posix())
    return names


def write_draft(runs_root: Path, built: Sequence[Mapping[str, Any]], *, staged_because: Sequence[str] = ()) -> str:
    """Not queued (queue writes off, or settings the runner cannot apply): keep what would have been."""
    name = f"{built[0]['plan']['job_id']}.json"
    path = confined(runs_root, str(CONTROL_ROOM_DIR), "drafts", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(confined(runs_root, str(CONTROL_ROOM_DIR), "drafts", name),
                      {"schema": DRAFT_SCHEMA, "queue_schema": nightly_cad.SCHEMA,
                       "staged_because": list(staged_because), "jobs": [item["job"] for item in built]})
    return path.relative_to(runs_root).as_posix()


def append_jobs(runs_root: Path, built: Sequence[Mapping[str, Any]]) -> int:
    """Append jobs to the nightly queue under its lock, through the runner's own load/save.

    The queue file and its lock are confined to runs/ (no symlinked ``runs`` or file).
    """
    queue_path = confined(runs_root, "nightly-cad-queue.json")
    confined(runs_root, "nightly-cad-queue.json.lock")
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
