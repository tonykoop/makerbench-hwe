"""Control Room: Morning Review of one finished nightly run folder (#1104 / #1105).

The review reads only what the nightly runner already wrote into the run folder:

- ``run_log.json``, through ``run_log_io.trial_rows()`` (the one trial reader);
- ``morning-summary.json`` (the folder is a "night" only once it exists);
- ``objective_scoreline.json`` and ``nightly-state.json`` when present;
- an optional ``brief.json`` and reference image for the brief rail.

Fields the runner does not record yet (per-turn passes, editable-parameter count,
the native CAD file) are read when a trial row carries them and otherwise shown as
"not recorded": nothing here invents a value.

Saving writes ``morning-review.json`` into the same folder. Revision requests are
planned as next-night jobs inside that file. They are appended to the Control Room
revision queue (``runs/control-room/revision-queue.json``) only when the Studio was
started with queue writes enabled (off by default). A revision continues one
entrant from its best turn, which the nightly queue schema cannot express (a
nightly job needs two entrants and a reference image), so the nightly queue file
itself is never written from here.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from ..arena_checks import retained_checks, trial_objective_rate
from ..code_cad_arena_runner import _trial_failed_checks
from ..run_log_io import atomic_write_json, file_lock, trial_rows

REVIEW_SCHEMA = "makerbench-control-room-morning-review-v1"
VIEW_SCHEMA = "makerbench-control-room-review-view-v1"
REVISION_QUEUE_SCHEMA = "makerbench-control-room-revision-queue-v1"
REVISION_QUEUE = Path("control-room") / "revision-queue.json"
SUMMARY_FILE = "morning-summary.json"
REVIEW_FILE = "morning-review.json"
BASE_CHECKS = ("renders", "watertight", "nonzero_volume", "fits_envelope", "body_count")
DECLARED_CHECKS = ("topology", "interfaces")
DEFAULT_EXTRA_TURNS = 3
MAX_EXTRA_TURNS = 12
MAX_NOTE_CHARS = 4000
#: Providers billed per call; any other entrant without a recorded charge ran on a subscription.
PAID_PROVIDERS = frozenset({"cadam", "openrouter"})
NATIVE_APPS = {
    ".fcstd": "FreeCAD",
    ".sldprt": "SolidWorks",
    ".sldasm": "SolidWorks",
    ".f3d": "Fusion",
    ".scad": "OpenSCAD",
}
REFERENCE_NAMES = ("reference.png", "reference.jpg", "reference.jpeg", "reference.webp")
SKIP_DIRS = frozenset({"workbench", ".makerbench-private"})


class ReviewError(ValueError):
    """A review payload that cannot be saved as sent."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _night_stamp(run_id: str) -> Optional[str]:
    """The nightly runner names runs ``<%Y%m%dT%H%M%SZ>-<job_id>``."""
    head = run_id.split("-", 1)[0]
    try:
        return datetime.strptime(head, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def discover_nights(runs_root: Path) -> list[Path]:
    """Run folders under ``runs/`` that hold a morning summary, newest first."""
    root = Path(runs_root)
    if not root.is_dir():
        return []
    found: dict[str, Path] = {}
    for summary in root.rglob(SUMMARY_FILE):
        run_dir = summary.parent
        relative = run_dir.relative_to(root).parts
        if any(part in SKIP_DIRS for part in relative):
            continue
        if not (run_dir / "run_log.json").is_file():
            continue
        found.setdefault(str(run_dir.resolve()), run_dir.resolve())
    return sorted(found.values(), key=lambda path: (_night_stamp(path.name) or "", path.name), reverse=True)


def _instrument(run_log: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    ids = _mapping(run_log.get("config")).get("instrument_ids") or []
    if ids:
        return str(ids[0])
    return next((str(row["instrument_id"]) for row in rows if row.get("instrument_id")), None)


def night_entry(run_dir: Path) -> dict[str, Any]:
    run_log = _mapping(_read_json(run_dir / "run_log.json"))
    brief = _mapping(_read_json(run_dir / "brief.json"))
    rows = trial_rows(run_log)
    instrument = _instrument(run_log, rows)
    return {
        "run_id": run_dir.name,
        "night": brief.get("night") or _night_stamp(run_dir.name) or run_log.get("started_at"),
        "instrument_id": instrument,
        "title": brief.get("title") or instrument or run_dir.name,
        "designs": len(rows),
        "reviewed": (run_dir / REVIEW_FILE).is_file(),
    }


def contained_file(run_dir: Path, raw: Any, repo_root: Path) -> Optional[Path]:
    """A recorded path, resolved, only when it is a file inside ``run_dir``."""
    if not isinstance(raw, str) or not raw:
        return None
    run_dir = run_dir.resolve()
    path = Path(raw)
    if not path.is_absolute():
        qualified = repo_root / path
        path = qualified if qualified.resolve().is_relative_to(run_dir) else run_dir / path
    path = path.resolve()
    return path if path.is_relative_to(run_dir) and path.is_file() else None


def _charges(state: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for charge in _mapping(state.get("budget")).get("charges") or []:
        if isinstance(charge, Mapping) and charge.get("entrant_id"):
            try:
                out[str(charge["entrant_id"])] = out.get(str(charge["entrant_id"]), 0.0) + float(charge.get("cost_usd") or 0)
            except (TypeError, ValueError):
                continue
    return out


def _check_list(objective: Mapping[str, Any]) -> list[dict[str, Any]]:
    scores = retained_checks(objective)
    names = [*BASE_CHECKS, *(name for name in DECLARED_CHECKS if name in scores)]
    out = []
    for name in names:
        score = scores.get(name)
        state = "unknown" if not isinstance(score, (int, float)) else ("pass" if score >= 1.0 else "fail")
        out.append({"name": name, "state": state, "declared": name in DECLARED_CHECKS})
    return out


def _fmt(value: Any, unit: Any) -> str:
    if isinstance(value, float):
        value = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value} {unit}".strip() if unit else str(value)


def failure_sentence(failure: Mapping[str, Any]) -> str:
    """One plain sentence for a failed check."""
    check = str(failure.get("check") or "a check")
    measured, threshold, unit = failure.get("measured"), failure.get("threshold"), failure.get("unit")
    if measured is None or threshold is None:
        detail = failure.get("detail")
        return f"The {check} check failed ({detail})." if detail else f"The {check} check failed."
    body = failure.get("body_id")
    where = f" ({body})" if body not in (None, "", "assembly") else ""
    if check == "fits_envelope":
        subject = f"The {body}" if body not in (None, "", "assembly") else "The design"
        return f"{subject} measures {_fmt(measured, unit)}; the envelope allows {_fmt(threshold, unit)}."
    if check == "body_count":
        noun = "separate body" if measured == 1 else "separate bodies"
        return f"The mesh has {measured} {noun}; the brief needs {threshold}."
    if check == "watertight":
        return f"The mesh is not closed{where}, so it cannot be printed or measured as a solid."
    if check == "nonzero_volume":
        return f"The part{where} has no volume ({_fmt(measured, unit)})."
    return f"The {check} check measured {_fmt(measured, unit)}{where}; it needs {_fmt(threshold, unit)}."


def _turns(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    turns = []
    for index, turn in enumerate(result.get("turns") or []):
        if not isinstance(turn, Mapping):
            continue
        passed, total = turn.get("passed"), turn.get("total") or len(BASE_CHECKS)
        if isinstance(passed, int) and isinstance(total, int) and total > 0:
            turns.append({"turn": int(turn.get("turn") or index + 1), "passed": passed, "total": total})
    return turns


def _best_turn(turns: Sequence[Mapping[str, Any]]) -> Optional[int]:
    if not turns:
        return None
    # The last turn with the most checks passing: later turns carry more fixes.
    return max(turns, key=lambda turn: (turn["passed"] / turn["total"], turn["turn"]))["turn"]


def _route(row: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    meta = _mapping(row.get("meta"))
    if meta.get("route"):
        return str(meta["route"])
    parts = [row.get("provider"), result.get("backend")]
    return " · ".join(str(part) for part in parts if part) or "route not recorded"


def _cut_list(path: Optional[Path]) -> list[dict[str, str]]:
    if path is None:
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)][:200]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def _design(run_dir: Path, repo_root: Path, row: Mapping[str, Any], charges: Mapping[str, float]) -> dict[str, Any]:
    result = _mapping(row.get("result"))
    objective = _mapping(result.get("objective"))
    meta = _mapping(row.get("meta"))
    trial_id = str(row.get("trial_id"))
    entrant = str(row.get("model_id") or trial_id)
    checks = _check_list(objective)
    failures = [failure_sentence(f) for f in _trial_failed_checks(row, result, objective)] if objective else []
    status = str(row.get("status") or "pending")
    if not objective:
        reason = row.get("error") or result.get("error") or f"it ended with status {status}"
        failures = [f"No graded design: {str(reason).strip()[:300]}"]
    artifacts = _mapping(result.get("artifacts"))
    native = _native_file(run_dir, repo_root, result)
    turns = _turns(result)
    provider = str(row.get("provider") or "")
    if entrant in charges:
        cost: dict[str, Any] = {"kind": "usd", "usd": round(charges[entrant], 4)}
    elif provider and provider not in PAID_PROVIDERS:
        cost = {"kind": "subscription"}
    else:
        cost = {"kind": "unknown"}
    editable = result.get("editable_parameters")
    design = {
        "trial_id": trial_id,
        "entrant": entrant,
        "name": str(meta.get("entrant_label") or entrant),
        "route": _route(row, result),
        "status": status,
        "checks": checks,
        "all_pass": bool(checks) and all(check["state"] == "pass" for check in checks),
        "failures": failures,
        "rate": trial_objective_rate(row),
        "editable_parameters": editable if isinstance(editable, int) else None,
        "time_s": row.get("wall_time_s") if isinstance(row.get("wall_time_s"), (int, float)) else None,
        "cost": cost,
        "turns": turns,
        "best_turn": _best_turn(turns),
        "has_mesh": contained_file(run_dir, artifacts.get("stl_path"), repo_root) is not None,
        "has_render": contained_file(run_dir, artifacts.get("png_path"), repo_root) is not None,
        "native": {"file": native.name, "app": NATIVE_APPS.get(native.suffix.lower(), "its app")} if native else None,
    }
    assembly = _mapping(result.get("assembly"))
    if assembly or result.get("track") == "wood_assembly":
        cut_list = _cut_list(contained_file(run_dir, assembly.get("cut_list_path"), repo_root))
        parts = [str(part) for part in assembly.get("parts") or [] if isinstance(part, str)]
        design["assembly"] = {"cut_list": cut_list, "parts": parts or sorted({r.get("part_id", "") for r in cut_list} - {""})}
    return design


def grader_order(designs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Best first: every check passing, then pass rate, then fewer turns, then id."""
    def key(design: Mapping[str, Any]):
        rate = design.get("rate")
        return (not design["all_pass"], -(rate if isinstance(rate, (int, float)) else -1.0),
                len(design.get("turns") or []), design["trial_id"])
    return [design["trial_id"] for design in sorted(designs, key=key)]


def review_view(run_dir: Path, repo_root: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    run_log = _mapping(_read_json(run_dir / "run_log.json"))
    summary = _mapping(_read_json(run_dir / SUMMARY_FILE))
    state = _mapping(_read_json(run_dir / "nightly-state.json"))
    brief = dict(_mapping(_read_json(run_dir / "brief.json")))
    rows = trial_rows(run_log)
    charges = _charges(state)
    designs = [_design(run_dir, repo_root, row, charges) for row in rows]
    finished = state.get("updated_utc") if state.get("status") not in (None, "running") else None
    reference = reference_path(run_dir)
    saved = _read_json(run_dir / REVIEW_FILE)
    entry = night_entry(run_dir)
    brief.pop("reference_image", None)
    return {
        "schema": VIEW_SCHEMA,
        **entry,
        "brief": {**brief, "has_reference": reference is not None},
        "summary": {
            "designs": len(designs),
            "all_pass": sum(1 for design in designs if design["all_pass"]),
            "cost_usd": summary.get("cost_usd"),
            "turns": sum(len(design["turns"]) for design in designs) if any(d["turns"] for d in designs) else None,
            "finished_utc": finished,
            "votable": bool(summary.get("votable")),
            "safety_block": summary.get("safety_block"),
        },
        "assembly": any("assembly" in design for design in designs),
        "designs": designs,
        "grader_order": grader_order(designs),
        "saved_review": saved if isinstance(saved, Mapping) and saved.get("schema") == REVIEW_SCHEMA else None,
        "morning_vote": _morning_vote_links(run_dir, summary),
    }


def reference_path(run_dir: Path) -> Optional[Path]:
    """The night's reference photo: a regular file directly in the run folder, never a symlink."""
    root = Path(run_dir).resolve()
    for name in REFERENCE_NAMES:
        path = Path(run_dir) / name
        if path.is_symlink() or not path.is_file():
            continue
        if path.resolve().parent == root:
            return path
    return None


def _morning_vote_links(run_dir: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    """Pointers to the blind morning-vote data for the same night (never the reveal)."""
    return {
        "summary": SUMMARY_FILE,
        "pair_files": [f"morning-vote/{name}" for name in summary.get("pair_files") or [] if isinstance(name, str)],
        "votes": [name for name in ("votes.blind.jsonl", "votes.revealed.jsonl") if (run_dir / name).is_file()],
    }


def build_review(view: Mapping[str, Any], payload: Mapping[str, Any], *, reviewer: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Validate a browser payload against the night it reviews; return the file body."""
    designs = {design["trial_id"]: design for design in view["designs"]}
    ranking = payload.get("ranking")
    if not isinstance(ranking, list) or sorted(map(str, ranking)) != sorted(designs):
        raise ReviewError("The ranking must list every design of this night exactly once.")
    notes_in = _mapping(payload.get("notes"))
    notes: dict[str, str] = {}
    for trial_id, note in notes_in.items():
        if trial_id not in designs:
            raise ReviewError(f"Unknown design in notes: {trial_id}")
        if not isinstance(note, str):
            raise ReviewError("Notes must be text.")
        if len(note) > MAX_NOTE_CHARS:
            raise ReviewError(f"A note is limited to {MAX_NOTE_CHARS} characters.")
        if note.strip():
            notes[trial_id] = note.strip()
    extra = payload.get("extra_turns", DEFAULT_EXTRA_TURNS)
    if not isinstance(extra, int) or isinstance(extra, bool) or not 1 <= extra <= MAX_EXTRA_TURNS:
        raise ReviewError(f"Extra turns must be a whole number from 1 to {MAX_EXTRA_TURNS}.")
    revise = payload.get("revise") or []
    if not isinstance(revise, list) or any(trial_id not in designs for trial_id in revise):
        raise ReviewError("Revision requests must name designs of this night.")
    requests = []
    for trial_id in [t for t in map(str, ranking) if t in set(revise)]:
        if trial_id not in notes:
            raise ReviewError(f"Write a note for {designs[trial_id]['name']} before sending it back for revision.")
        design = designs[trial_id]
        requests.append({
            "trial_id": trial_id,
            "entrant": design["entrant"],
            "from_turn": design["best_turn"] if design["best_turn"] is not None else "best",
            "extra_turns": extra,
            "feedback": notes[trial_id],
        })
    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    return {
        "schema": REVIEW_SCHEMA,
        "run_id": view["run_id"],
        "night": view.get("night"),
        "instrument_id": view.get("instrument_id"),
        "reviewer": reviewer,
        "saved_utc": stamp,
        "grader_order": list(view["grader_order"]),
        "ranking": [str(trial_id) for trial_id in ranking],
        "notes": notes,
        "revision_requests": requests,
        "morning_vote": view["morning_vote"],
        "queued": False,
    }


def next_night_jobs(review: Mapping[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    """The revision-queue jobs a review's revision requests stand for."""
    if not review.get("revision_requests"):
        return []
    state = _mapping(_read_json(run_dir / "nightly-state.json"))
    job_id = str(state.get("job_id") or review["run_id"])
    return [
        {
            "job_id": f"{job_id}-revise-{request['entrant']}"[:120],
            "instrument_id": review.get("instrument_id"),
            "status": "queued",
            "revision_of": {
                "run_id": review["run_id"],
                "trial_id": request["trial_id"],
                "from_turn": request["from_turn"],
                "extra_turns": request["extra_turns"],
                "feedback": request["feedback"],
            },
            "entrants": [{"entrant_id": request["entrant"]}],
        }
        for request in review["revision_requests"]
    ]


class QueueError(ValueError):
    """The revision queue cannot be written where it should be."""


def confined_queue_path(runs_root: Path) -> Path:
    """``runs/control-room/revision-queue.json``, refusing any symlink on the way out of ``runs/``."""
    root = Path(runs_root)
    folder = root / REVISION_QUEUE.parent
    for path in (root, folder, folder / REVISION_QUEUE.name):
        if path.is_symlink():
            raise QueueError(f"Refusing to write the revision queue through a symlink ({path.name}).")
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.resolve().is_relative_to(root.resolve()):
        raise QueueError("The revision queue folder resolves outside runs/.")
    return folder / REVISION_QUEUE.name


def append_to_queue(queue_path: Path, jobs: Sequence[Mapping[str, Any]], *, run_id: str) -> dict[str, int]:
    """Reconcile this night's pending revision jobs with the latest save, under the queue lock.

    The latest save wins: a still-queued job for the same design is replaced with the new
    note, and a queued job for a design no longer sent back is withdrawn. Jobs a runner has
    already picked up (status other than ``queued``) are never touched.
    """
    with file_lock(queue_path):
        payload = _read_json(queue_path) if queue_path.is_file() else None
        payload = dict(payload) if isinstance(payload, Mapping) else {"schema": REVISION_QUEUE_SCHEMA}
        wanted = {job["job_id"]: dict(job) for job in jobs}
        kept, counts = [], {"added": 0, "replaced": 0, "withdrawn": 0}
        for job in payload.get("jobs") or []:
            if not isinstance(job, Mapping):
                continue
            ours = _mapping(job.get("revision_of")).get("run_id") == run_id and job.get("status") == "queued"
            if ours and job.get("job_id") in wanted:
                kept.append(wanted.pop(job["job_id"]))
                counts["replaced"] += 1
            elif ours:
                counts["withdrawn"] += 1
            else:
                kept.append(dict(job))
                wanted.pop(job.get("job_id"), None)  # a runner already has it: leave it alone
        counts["added"] = len(wanted)
        payload["jobs"] = kept + list(wanted.values())
        atomic_write_json(queue_path, payload)
    return counts


def save_review(run_dir: Path, review: Mapping[str, Any]) -> Path:
    path = Path(run_dir) / REVIEW_FILE
    atomic_write_json(path, review)
    return path


def windows_opener(path: Path) -> None:
    """Open a file in its Windows app from WSL (or natively on Windows)."""
    if os.name == "nt":  # pragma: no cover - Studio runs under WSL
        os.startfile(str(path))  # noqa: S606 - a contained run-folder file
        return
    wslpath, explorer = shutil.which("wslpath"), shutil.which("explorer.exe")
    if not wslpath or not explorer:
        raise OSError("Opening native files needs Windows (WSL with explorer.exe).")
    windows = subprocess.run([wslpath, "-w", str(path)], check=True, capture_output=True, text=True).stdout.strip()
    # explorer.exe exits 1 even on success, so its status is not checked.
    subprocess.run([explorer, windows], check=False, timeout=30)


def native_path(run_dir: Path, repo_root: Path, trial_id: str) -> Optional[Path]:
    run_log = _mapping(_read_json(run_dir / "run_log.json"))
    row = next((r for r in trial_rows(run_log) if str(r.get("trial_id")) == trial_id), None)
    if row is None:
        return None
    return _native_file(run_dir, repo_root, _mapping(row.get("result")))


def _native_file(run_dir: Path, repo_root: Path, result: Mapping[str, Any]) -> Optional[Path]:
    """The design's native CAD file, only with a CAD suffix (it is handed to the OS file opener)."""
    for raw in (result.get("native_path"), result.get("scad_path") or _mapping(result.get("gen")).get("scad_path")):
        path = contained_file(run_dir, raw, repo_root)
        if path is not None and path.suffix.lower() in NATIVE_APPS:
            return path
    return None


def trial_artifact(run_dir: Path, repo_root: Path, trial_id: str, key: str, suffix: str) -> Optional[Path]:
    run_log = _mapping(_read_json(run_dir / "run_log.json"))
    row = next((r for r in trial_rows(run_log) if str(r.get("trial_id")) == trial_id), None)
    if row is None:
        return None
    path = contained_file(run_dir, _mapping(_mapping(row.get("result")).get("artifacts")).get(key), repo_root)
    return path if path is not None and path.suffix.lower() == suffix else None


Opener = Callable[[Path], None]
