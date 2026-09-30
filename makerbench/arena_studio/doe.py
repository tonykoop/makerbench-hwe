"""DoE Matrix Builder for MakerBench Arena Studio (Issue #697, Epic #694).

Expands instruments x models x levels x context tiers x seeds into a
deduplicated trial-cell list, attaches an honest time/cost estimate per
model from the post-mortem telemetry store (#640), and can emit a queue
file compatible with the nightly resumable queue schema
(:data:`makerbench.nightly_cad.SCHEMA`, #647).

This module never executes anything: no subprocess, no CLI dispatch, no
network call. It only previews a matrix and, optionally, writes a queue
file to disk for a human (or the nightly runner) to pick up later.

Level/context-tier note: ``nightly_cad``'s ``NightlyJob``/``NightlyEntrant``
schema (#647) already has ``backend`` and ``kind`` fields, which are reused.
Live cells use the existing ``model_id`` as their driver model. No new nightly
schema fields are added. The schema has no ``level`` field. One DoE cell's ``level`` is folded into
``entrant_id`` as ``"<model_id>::<level>"`` instead — ``NightlyEntrant.model_id``
stays the real, dispatchable model id, and ``entrant_id`` (already a
free-form dedup/display key in that schema) carries the level so a queue
consumer can still tell cells apart without a schema change.

Cost-estimate note: per-token pricing tables in ``makerbench.pricing`` are
intentionally *not* chained in here. Their model-name keys (e.g.
``claude-opus-4-8``) don't match this arena's CLI-prefixed model ids (e.g.
``claude-code-opus-5``), and guessing a mapping would produce a
false-confidence number, not an honest one. Cost is either a known $0
(subscription CLI lane), a historical average from the telemetry store, or
explicitly unknown.
"""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import os
import math
import shutil
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from .. import code_cad_providers as providers
from .. import nightly_cad

try:
    from telemetry.store import read_all as _read_telemetry_sessions
except ImportError:  # pragma: no cover - telemetry package is optional at import time
    _read_telemetry_sessions = None


SCHEMA = "makerbench-arena-studio-doe-v1"
DEFAULT_LEVELS = ("L1", "L2", "L3", "L4")
DEFAULT_CONTEXT_TIERS = ("blind",)
DEFAULT_BACKENDS = ("openscad",)
BACKENDS = (
    "openscad",
    "cadquery",
    "build123d",
    "blender",
    "solidworks",
    "fusion",
    "solidworks-live",
    "fusion-live",
)
LIVE_BACKENDS = ("solidworks-live", "fusion-live")


def backend_availability(backend: str) -> dict:
    """Read-only local hints; never probe a connector or create a jobs directory.

    A Windows mount or installed wheel cannot prove a watcher, authenticated
    connector, or sandbox works. Such lanes require execution-time preflight.
    """
    if backend not in BACKENDS:
        raise DoeValidationError(f"unsupported backend: {backend}")
    if backend in LIVE_BACKENDS and not shutil.which("codex"):
        return {"status": "unavailable", "reason": "Codex live driver not installed"}
    if backend.startswith(("solidworks", "fusion")):
        if os.name != "nt" and not Path("/mnt/c").is_dir():
            return {"status": "unavailable", "reason": "Windows bridge unavailable"}
        return {"status": "requires_preflight", "reason": "Windows app/bridge readiness unverified"}
    if backend in ("cadquery", "build123d"):
        if importlib.util.find_spec(backend) is None:
            return {"status": "unavailable", "reason": f"{backend} runtime not installed"}
        if not shutil.which("bwrap") or not shutil.which("openscad"):
            return {"status": "unavailable", "reason": "bwrap and OpenSCAD required"}
        return {"status": "requires_preflight", "reason": "Sandbox readiness unverified"}
    if not shutil.which(backend):
        return {"status": "unavailable", "reason": f"{backend} executable not installed"}
    return {"status": "available", "reason": "Local executable found"}


class DoeValidationError(ValueError):
    """The DoE request itself is invalid (fix the input), not a server failure.

    The Studio routes answer these with HTTP 400; anything else stays a 500.
    """


class DoeQueueExistsError(Exception):
    """A queue file already exists for this run name; replacing it needs explicit confirmation."""


DEFAULT_TELEMETRY_STORE = "data/sessions.jsonl"

# CLI providers billed under an existing subscription (see
# code_cad_providers._PROVIDER_PREFIXES for the model-id -> provider mapping):
# one more arena trial's marginal cost really is $0.00 -- a known fact, not a
# guess. "openrouter" is deliberately excluded: it is metered API spend.
_SUBSCRIPTION_PROVIDERS = {"claude", "codex", "gemini", "agy", "stub"}


def _cell_id(
    instrument_id: str,
    model_id: str,
    level: str,
    context_tier: str,
    seed: int,
    backend: str = "openscad",
    driver_model: Optional[str] = None,
) -> str:
    raw = f"{instrument_id}|{model_id}|{level}|{context_tier}|{seed}"
    if backend != "openscad" or driver_model is not None:
        raw += f"|{backend}|{driver_model or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def expand_matrix(
    instruments: Iterable[str],
    models: Iterable[str],
    *,
    levels: Iterable[str] = DEFAULT_LEVELS,
    context_tiers: Iterable[str] = DEFAULT_CONTEXT_TIERS,
    seeds: Iterable[int] = (0,),
    backends: Iterable[str] = DEFAULT_BACKENDS,
    driver_models: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Deduplicated cells including backend and, for live lanes, driver model.

    The legacy hash input is unchanged for OpenSCAD. Live model_id holds the
    driver consumed by NightlyExecutor._run_live; explicit driver_models replace
    nominal models for live cells, never multiply identical live entrants.
    """

    instruments, models, levels, context_tiers, seeds, backends = (
        tuple(axis) for axis in (instruments, models, levels, context_tiers, seeds, backends)
    )
    drivers = tuple(driver_models) if driver_models is not None else None
    if not all((instruments, models, levels, context_tiers, seeds, backends)):
        raise DoeValidationError("matrix axes must be nonempty")
    if drivers is not None and not drivers:
        raise DoeValidationError("driver_models must be nonempty when supplied")
    for backend in backends:
        if backend not in BACKENDS:
            raise DoeValidationError(f"unsupported backend: {backend}")
    has_live = any(backend in LIVE_BACKENDS for backend in backends)
    if has_live and drivers is None:
        raise DoeValidationError("live backends require explicit driver_models")
    if drivers is not None and not has_live:
        raise DoeValidationError("driver_models only apply to live backends")
    seen: set[str] = set()
    cells: list[dict] = []
    for instrument_id, model_id, level, context_tier, seed, backend in itertools.product(
        instruments, models, levels, context_tiers, seeds, backends
    ):
        if backend in LIVE_BACKENDS and context_tier == "studio":
            raise DoeValidationError("live backends do not support studio context")
        for driver in drivers if backend in LIVE_BACKENDS else (None,):
            effective_model = driver if driver is not None else model_id
            cell_id = _cell_id(
                instrument_id, effective_model, level, context_tier, seed, backend, driver
            )
            if cell_id in seen:
                continue
            seen.add(cell_id)
            cells.append(
                {
                    "cell_id": cell_id,
                    "instrument_id": instrument_id,
                    "model_id": effective_model,
                    "level": level,
                    "context_tier": context_tier,
                    "seed": seed,
                    "backend": backend,
                    "driver_model": driver,
                }
            )
    return cells


MATCHUP_AXES = (
    "instruments",
    "models",
    "levels",
    "context_tiers",
    "seeds",
    "backends",
    "driver_models",
)
_AXIS_ALIASES = {
    "instrument": "instruments",
    "model": "models",
    "level": "levels",
    "context": "context_tiers",
    "context_tier": "context_tiers",
    "seed": "seeds",
    "backend": "backends",
    "bridge": "backends",
    "driver_model": "driver_models",
}


def build_matchup(
    varied_axis: str,
    values: Iterable,
    *,
    instruments: Iterable[str],
    models: Iterable[str],
    levels: Iterable[str] = ("L1",),
    context_tiers: Iterable[str] = DEFAULT_CONTEXT_TIERS,
    seeds: Iterable[int] = (0,),
    backends: Iterable[str] = DEFAULT_BACKENDS,
    driver_models: Optional[Iterable[str]] = None,
    factorial: bool = False,
) -> dict:
    """Preview a controlled matchup, rejecting accidental multi-axis experiments.

    Values are dispatch identifiers, never inferred from display labels.
    Live matchups vary driver_models, not nominal models which the live runner
    does not consume. Metadata is retained alongside cells and in queue files.
    """
    axis = _AXIS_ALIASES.get(varied_axis, varied_axis)
    if axis not in MATCHUP_AXES:
        raise DoeValidationError(f"unsupported varied_axis: {varied_axis}")
    dimensions = {
        "instruments": list(instruments),
        "models": list(models),
        "levels": list(levels),
        "context_tiers": list(context_tiers),
        "seeds": list(seeds),
        "backends": list(backends),
        "driver_models": list(driver_models) if driver_models is not None else [],
    }
    selected = list(dict.fromkeys(values))
    if axis == "seeds":
        try:
            if any(isinstance(value, (bool, float)) for value in selected):
                raise ValueError("seeds must be integers")
            selected = list(dict.fromkeys(int(value) for value in selected))
        except (ValueError, TypeError):
            raise DoeValidationError("seeds must be integers") from None
    elif any(not isinstance(value, str) or not value.strip() for value in selected):
        raise DoeValidationError("axis values must be nonempty strings")
    if len(selected) < 2:
        raise DoeValidationError("a matchup needs at least two distinct axis values")
    # Conflicting values for the chosen axis must not disappear behind --values.
    original = list(dict.fromkeys(dimensions[axis]))
    if len(original) > 1 and set(original) != set(selected):
        raise DoeValidationError("selected values conflict with the varied axis")
    dimensions[axis] = selected
    dimensions = {name: list(dict.fromkeys(items)) for name, items in dimensions.items()}
    if not all(dimensions[name] for name in MATCHUP_AXES if name != "driver_models"):
        raise DoeValidationError("held axes must be nonempty")
    live = any(backend in LIVE_BACKENDS for backend in dimensions["backends"])
    if axis == "driver_models" and not all(b in LIVE_BACKENDS for b in dimensions["backends"]):
        raise DoeValidationError("driver_models matchups require live backends")
    if live and axis == "models":
        raise DoeValidationError("live matchups vary driver_models, not nominal models")
    if dimensions["driver_models"] and not live:
        raise DoeValidationError("driver_models only apply to live backends")
    if live and not dimensions["driver_models"]:
        raise DoeValidationError("live backends require explicit driver_models")
    if (
        live
        and any(b not in LIVE_BACKENDS for b in dimensions["backends"])
        and len(dimensions["models"]) == len(dimensions["driver_models"]) == 1
        and dimensions["models"] != dimensions["driver_models"]
    ):
        raise DoeValidationError(
            "mixed live/code-CAD backends must hold the same effective model; "
            "the held driver model must equal the held nominal model"
        )
    varied = [name for name, items in dimensions.items() if len(items) > 1]
    if len(varied) > 1 and not factorial:
        raise DoeValidationError(f"matchups vary one axis; varying {varied!r} requires factorial")
    if (
        live
        and len(dimensions["models"]) > 1
        and all(b in LIVE_BACKENDS for b in dimensions["backends"])
    ):
        raise DoeValidationError("nominal models are unused by live backends; hold one model")
    cells = expand_matrix(**{**dimensions, "driver_models": dimensions["driver_models"] or None})
    held = {name: items[0] for name, items in dimensions.items() if len(items) == 1}
    return {
        "schema": SCHEMA,
        "varied_axis": axis,
        "values": selected,
        "varied_axes": varied,
        "factorial": factorial,
        "held": held,
        "cells": cells,
    }


def _model_provider(model_id: str) -> Optional[str]:
    try:
        return providers.provider_for_model_id(model_id)
    except ValueError:
        return None


def _load_sessions(telemetry_store: str) -> list:
    if _read_telemetry_sessions is None:
        return []
    try:
        return _read_telemetry_sessions(telemetry_store)
    except (FileNotFoundError, OSError):
        return []


def _session_cost(session) -> Optional[float]:
    # SessionTelemetry (#640) has no dedicated cost field today; look for one
    # under its free-form telemetry dict without assuming it is there.
    telemetry = getattr(session, "telemetry", None) or {}
    value = telemetry.get("cost_usd")
    return float(value) if (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    ) else None


def _historical_duration(model_id: str, telemetry_store: str, backend: str = "openscad") -> dict:
    matching = [
        s
        for s in _load_sessions(telemetry_store)
        if getattr(s, "agent_id", None) == model_id
        and (getattr(s, "telemetry", None) or {}).get("backend", "openscad") == backend
    ]
    durations = [s.duration_seconds for s in matching
                 if math.isfinite(s.duration_seconds) and s.duration_seconds >= 0]
    if not durations:
        return {"duration_s": None, "n_duration_samples": 0}
    avg = sum(durations) / len(durations)
    return {"duration_s": round(avg, 1), "n_duration_samples": len(durations)}


def _historical_cost(model_id: str, telemetry_store: str, backend: str = "openscad") -> dict:
    sessions = _load_sessions(telemetry_store)
    costed = [
        (s, _session_cost(s))
        for s in sessions
        if getattr(s, "agent_id", None) == model_id
        and (getattr(s, "telemetry", None) or {}).get("backend", "openscad") == backend
    ]
    costs = [cost for _s, cost in costed if cost is not None]
    if not costs:
        return {"cost_usd": None, "cost_source": "unknown", "n_cost_samples": 0}
    return {
        "cost_usd": round(sum(costs) / len(costs), 6),
        "cost_source": f"telemetry_average:{telemetry_store}",
        "n_cost_samples": len(costs),
    }


def estimate_model_cost_and_time(
    model_id: str, *, telemetry_store: str = DEFAULT_TELEMETRY_STORE, backend: str = "openscad"
) -> dict:
    """Historical time/cost estimate for one model (#640 post-mortem telemetry).

    Never fabricates a number: a subscription CLI model's marginal cost is a
    known $0.00; everything else without prior telemetry reports
    ``cost_usd: None`` / ``cost_source: "unknown"`` instead of defaulting to
    $0.00.
    """

    provider = _model_provider(model_id)
    if provider in _SUBSCRIPTION_PROVIDERS or backend in LIVE_BACKENDS:
        cost_result = {
            "cost_usd": 0.0,
            "cost_source": "subscription_zero_marginal",
            "n_cost_samples": 0,
        }
    else:
        cost_result = _historical_cost(model_id, telemetry_store, backend)

    return {**cost_result, **_historical_duration(model_id, telemetry_store, backend)}


def annotate_matrix_with_estimates(
    cells: list[dict], *, telemetry_store: str = DEFAULT_TELEMETRY_STORE
) -> list[dict]:
    """Attach a time/cost estimate to each cell, memoized per model and backend."""

    cache: dict[tuple[str, str], dict] = {}
    availability: dict[str, dict] = {}
    annotated = []
    for cell in cells:
        model_id = cell["model_id"]
        backend = cell.get("backend", "openscad")
        key = (model_id, backend)
        if key not in cache:
            cache[key] = estimate_model_cost_and_time(
                model_id, telemetry_store=telemetry_store, backend=backend
            )
        if backend not in availability:
            availability[backend] = backend_availability(backend)
        annotated.append({**cell, "estimate": cache[key], "availability": availability[backend]})
    return annotated


def matrix_summary(annotated_cells: list[dict]) -> dict:
    """Roll up the annotated matrix into totals for the DoE preview UI (#697)."""

    known_cost_total = 0.0
    has_unknown_cost = False
    known_time_total = 0.0
    has_unknown_time = False

    for cell in annotated_cells:
        estimate = cell.get("estimate") or {}
        cost = estimate.get("cost_usd")
        if cost is None:
            has_unknown_cost = True
        else:
            known_cost_total += float(cost)
        duration = estimate.get("duration_s")
        if duration is None:
            has_unknown_time = True
        else:
            known_time_total += float(duration)

    return {
        "n_cells": len(annotated_cells),
        "backends": sorted({c.get("backend", "openscad") for c in annotated_cells}),
        "driver_models": sorted(
            {c["driver_model"] for c in annotated_cells if c.get("driver_model")}
        ),
        "n_unavailable_cells": sum(
            c.get("availability", {}).get("status") == "unavailable" for c in annotated_cells
        ),
        "n_preflight_cells": sum(
            c.get("availability", {}).get("status") == "requires_preflight" for c in annotated_cells
        ),
        "known_cost_usd": round(known_cost_total, 6),
        "has_unknown_cost_cells": has_unknown_cost,
        "known_time_s": round(known_time_total, 1),
        "has_unknown_time_cells": has_unknown_time,
    }


def resolve_max_cost_usd_by_model(
    model_ids: Iterable[str],
    *,
    overrides: Optional[Mapping[str, float]] = None,
    telemetry_store: str = DEFAULT_TELEMETRY_STORE,
) -> dict[str, float]:
    """Resolve a real per-model cost ceiling for the nightly budget guard (#709).

    Fails closed: a model with no known cost (``cost_source: "unknown"`` --
    e.g. a metered API model with no telemetry history) raises rather than
    being defaulted to ``$0.00``. ``nightly_cad.BudgetGuard.reserve()`` treats
    ``max_cost_usd`` as a falsy-check "no cap at all" when it is ``0.0``, so a
    silent ``0.0`` default for an unknown-cost model would mean that model's
    spend is never capped -- exactly backwards for the metered/paid entrants
    this ceiling exists to protect against. Callers with a known real ceiling
    for a model (e.g. an operator-supplied budget) pass it via ``overrides``.
    """

    overrides = overrides or {}
    resolved: dict[str, float] = {}
    unresolved: list[str] = []
    for model_id in set(model_ids):
        override = overrides.get(model_id)
        if override is not None:
            # NaN compares false against everything, so a NaN "ceiling" would
            # silently disable BudgetGuard's reserve/charge checks; infinities
            # are no cap at all. Only a finite positive dollar amount is a ceiling.
            if not math.isfinite(override) or override <= 0:
                unresolved.append(model_id)
                continue
            resolved[model_id] = float(override)
            continue

        estimate = estimate_model_cost_and_time(model_id, telemetry_store=telemetry_store)
        if estimate["cost_source"] == "subscription_zero_marginal":
            resolved[model_id] = 0.0
        elif estimate["cost_usd"] is not None:
            resolved[model_id] = float(estimate["cost_usd"])
        else:
            unresolved.append(model_id)

    if unresolved:
        raise DoeValidationError(
            "no known cost ceiling for model(s) "
            f"{sorted(unresolved)!r}; pass an explicit positive max_cost_usd "
            "override for each -- an unknown cost must never silently "
            "default to $0.00 (nightly_cad.BudgetGuard.reserve() treats that "
            "as 'no cap', not 'known free')"
        )
    return resolved


def build_nightly_queue(
    cells: list[dict],
    *,
    reference_images: Mapping[str, str],
    is_approved: Optional[Callable[[str], bool]] = None,
    budget_usd: float = 5.0,
    max_cost_usd_by_model: Optional[Mapping[str, float]] = None,
) -> tuple[dict, list["nightly_cad.NightlyJob"]]:
    """Group DoE cells into ``nightly_cad.NightlyJob`` objects (#647 schema).

    One job per ``(instrument_id, seed, context_tier)``; its entrants are
    every model x level cell in that group. A group whose instrument has no
    approved reference image (per ``is_approved``, the #697/#699 D4
    gatekeeper) or no on-disk reference image is skipped, never silently
    dropped — it is reported in the returned payload's ``skipped`` list.
    """

    max_cost_usd_by_model = max_cost_usd_by_model or {}
    groups: dict[tuple[str, int, str], list[dict]] = {}
    for cell in cells:
        if cell.get("backend", "openscad") not in BACKENDS:
            raise DoeValidationError(f"unsupported backend: {cell['backend']}")
        key = (cell["instrument_id"], cell["seed"], cell["context_tier"])
        groups.setdefault(key, []).append(cell)

    jobs: list[nightly_cad.NightlyJob] = []
    skipped: list[dict] = []
    for (instrument_id, seed, context_tier), group_cells in groups.items():
        if is_approved is not None and not is_approved(instrument_id):
            skipped.append(
                {"instrument_id": instrument_id, "reason": "reference_image_not_approved"}
            )
            continue

        reference_image = reference_images.get(instrument_id)
        if not reference_image or not Path(reference_image).is_file():
            skipped.append({"instrument_id": instrument_id, "reason": "no_reference_image_on_disk"})
            continue

        entrants = [
            nightly_cad.NightlyEntrant(
                entrant_id=(
                    f"{cell['model_id']}::{cell['level']}"
                    + (
                        f"::{cell['backend']}"
                        if cell.get("backend", "openscad") != "openscad"
                        else ""
                    )
                ),
                kind="live" if cell.get("backend") in LIVE_BACKENDS else "arena",
                model_id=cell["model_id"],
                backend=cell.get("backend", "openscad"),
                context_tier=context_tier,
                max_cost_usd=max_cost_usd_by_model.get(cell["model_id"], 0.0),
            )
            for cell in group_cells
        ]
        if len(entrants) < 2:
            skipped.append({"instrument_id": instrument_id, "reason": "fewer_than_two_entrants"})
            continue

        job = nightly_cad.NightlyJob(
            job_id=f"doe-{instrument_id}-seed{seed}-{context_tier}",
            instrument_id=instrument_id,
            reference_image=str(reference_image),
            entrants=entrants,
            seed=seed,
            budget_usd=budget_usd,
        )
        job.validate()
        jobs.append(job)

    backend_status = {
        backend: backend_availability(backend)
        for backend in sorted({c.get("backend", "openscad") for c in cells})
    }
    payload = {
        "schema": nightly_cad.SCHEMA,
        "generated_by": SCHEMA,
        "skipped": skipped,
        "backend_warnings": [
            {"backend": backend, **status}
            for backend, status in backend_status.items()
            if status["status"] != "available"
        ],
    }
    return payload, jobs


def write_queue_file(
    path: Path, payload: Mapping[str, object], jobs: list["nightly_cad.NightlyJob"]
) -> None:
    """Write the queue file via ``nightly_cad``'s own atomic writer (#647).

    This never dispatches or executes a job — it only writes the file that a
    separate, explicitly-invoked nightly runner would later read.
    """

    nightly_cad.save_queue(Path(path), payload, jobs)
