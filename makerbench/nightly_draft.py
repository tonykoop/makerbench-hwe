"""Draft one nightly matchup job through the Control Room New Run code path (#663 / #1114).

The job is built exactly as New Run's "Add to tonight" builds it (``validate_form``,
``runner_gaps``, ``build_jobs``: the same entrant ids, cost split and reference copy), then
written to a separate **draft** queue file for review and preflight. The live nightly queue
is never written here: a destination named like a live queue (``queue.json``,
``nightly-cad-queue.json``), the scheduled task's queue path or the runs root's queue is
refused, as is a draft that the runner would run differently from its plan.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import nightly_cad
from .arena_studio import control_room_newrun as nr
from .arena_studio import instrument_repo as ir
from .code_cad_arena_runner import load_arena_registry
from .run_log_io import atomic_write_json

LIVE_QUEUE_NAME = "nightly-cad-queue.json"
LIVE_QUEUE_NAMES = (LIVE_QUEUE_NAME, "queue.json")
#: The queue the ``makerbench-nightly-cad`` scheduled task runs (docs/NIGHTLY_CAD_TASK.md).
TASK_QUEUE = Path("/mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-nightly/queue.json")
DRAFT_SOURCE = "makerbench arena draft-matchup (Control Room New Run code path)"


class DraftError(ValueError):
    """A draft that cannot be written as asked (message is user-facing)."""


def draft_matchup(
    *,
    instrument: str,
    entrants: Sequence[str],
    registry_path: Path,
    runs_root: Path,
    out_path: Path,
    reference: Optional[str] = None,
    instruments_root: Optional[Path] = None,
    cap_usd: float = 2.0,
    replace: bool = False,
    now: Optional[datetime] = None,
    live_queues: Sequence[Path] = (TASK_QUEUE,),
) -> dict[str, Any]:
    """Build the job with New Run and write it, alone, to ``out_path``. Returns ``{job, plan, out}``."""
    out_path = Path(out_path)
    live = [Path(runs_root) / LIVE_QUEUE_NAME, *map(Path, live_queues)]
    if out_path.name in LIVE_QUEUE_NAMES or any(out_path.resolve() == queue.resolve() for queue in live):
        raise DraftError(f"Refusing to write the live nightly queue ({out_path}); give a draft file name.")
    if out_path.exists() and not replace:
        raise DraftError(f"{out_path} already exists; pass --replace to overwrite the draft.")
    registry = load_arena_registry(Path(registry_path))
    tasks = [task for task in registry.get("instruments") or [] if isinstance(task, Mapping)]
    cards = {card["id"]: card for card in nr.registry_instruments(tasks)}
    card = cards.get(instrument)
    if card is None:
        raise DraftError(f"{instrument} is not an arena registry task.")
    repo = ir.find_repo(instruments_root, instrument, ir.registry_repo_path(tasks, instrument)) if instruments_root else None
    form = {
        "instrument": instrument,
        "entrants": list(entrants),
        "brief": card["brief"],            # the registry brief: what the runner sends
        "turns": nr.RUNNER_TURNS,
        "cap_usd": float(cap_usd),
        "deliverable": "single",
        "variants": [],
        "references": [{"repo_image": reference}] if reference else [],
        "stop_early": True,
    }
    gaps = nr.runner_gaps(form, card)
    if gaps:
        raise DraftError("The nightly runner would not apply: " + "; ".join(gaps))
    if reference and repo is None:
        raise DraftError(f"No local instrument repo for {instrument} under {instruments_root}.")
    try:
        built = nr.build_jobs(
            form, instruments=cards, runs_root=Path(runs_root), now=now,
            task_reference=lambda _task: None,
            repo_image=(lambda name: ir.repo_file(repo, f"images/{name}")) if repo is not None else None,
        )
        plans = nr.write_plans(Path(runs_root), built)
    except (nr.NewRunError, nr.UnsafePath) as exc:
        raise DraftError(str(exc)) from None
    job = built[0]["job"]
    nightly_cad.NightlyJob.from_dict(job).validate()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_path, {
        "schema": nightly_cad.SCHEMA,
        "draft": True,
        "drafted_by": DRAFT_SOURCE,
        "plan": plans[0],
        "jobs": [job],
    })
    return {"job": job, "plan": built[0]["plan"], "out": out_path.as_posix()}
