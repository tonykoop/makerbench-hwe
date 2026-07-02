"""3D viewer assets and winner export for the Code-CAD Arena (#602, #603).

Two concerns live here because they share the STL -> GLB conversion:

* Blind voting needs rotatable 3D models (a single static PNG hides logical
  mistakes — Round 1 voter feedback), so every rendered candidate gets a GLB
  with each disjoint body tinted a distinct color for part identification.
* After voting closes, each instrument's winning CAD model is exported into
  that instrument's build repo as a durable artifact (arena ``runs/`` are
  gitignored and ephemeral).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional


# Qualitative palette for assembly part identification (distinct hues that
# survive both light and dark viewer backgrounds).
BODY_COLORS: tuple[tuple[int, int, int, int], ...] = (
    (214, 86, 43, 255),   # rust orange
    (58, 122, 186, 255),  # steel blue
    (106, 168, 79, 255),  # leaf green
    (191, 144, 0, 255),   # ochre
    (142, 99, 173, 255),  # violet
    (64, 145, 140, 255),  # teal
    (166, 77, 90, 255),   # brick rose
    (120, 120, 116, 255), # warm gray
)


def stl_to_glb(stl_path: Path, glb_path: Path) -> Path:
    """Convert an STL to a GLB scene with one distinctly-colored mesh per body."""

    import trimesh

    mesh = trimesh.load(Path(stl_path).as_posix(), force="mesh")
    try:
        bodies = list(mesh.split(only_watertight=False))
    except Exception:  # noqa: BLE001 - degenerate meshes still get a viewer.
        bodies = []
    if not bodies:
        bodies = [mesh]
    scene = trimesh.Scene()
    for index, body in enumerate(bodies):
        body.visual = trimesh.visual.ColorVisuals(
            body, face_colors=BODY_COLORS[index % len(BODY_COLORS)]
        )
        scene.add_geometry(body, node_name=f"body_{index}")
    glb_path = Path(glb_path)
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    glb_path.write_bytes(scene.export(file_type="glb"))
    return glb_path


def ensure_glb(stl_path: Path) -> Optional[Path]:
    """Return the sibling ``output.glb`` for an STL, converting lazily.

    Lazy conversion is what makes existing runs (Round 1) retroactively
    viewable in 3D. Returns None when the STL is missing or conversion fails.
    """

    stl = Path(stl_path)
    if not stl.exists():
        return None
    glb = stl.with_suffix(".glb")
    if glb.exists() and glb.stat().st_mtime >= stl.stat().st_mtime:
        return glb
    try:
        return stl_to_glb(stl, glb)
    except Exception:  # noqa: BLE001 - voting falls back to the static PNG.
        return None


def instrument_vote_tallies(revealed_jsonl: Path) -> dict[str, dict[str, float]]:
    """Per-instrument entrant win tallies from revealed votes (win 1, draw 0.5)."""

    tallies: dict[str, dict[str, float]] = {}
    path = Path(revealed_jsonl)
    if not path.exists():
        return tallies
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        instrument = str(record.get("instrument_id") or "")
        reveal = record.get("reveal") or {}
        left = str((reveal.get("left") or {}).get("model_id") or "")
        right = str((reveal.get("right") or {}).get("model_id") or "")
        winner = str(record.get("winner") or "")
        if not instrument or not left or not right:
            continue
        bucket = tallies.setdefault(instrument, {})
        bucket.setdefault(left, 0.0)
        bucket.setdefault(right, 0.0)
        if winner == "left":
            bucket[left] += 1.0
        elif winner == "right":
            bucket[right] += 1.0
        elif winner == "draw":
            bucket[left] += 0.5
            bucket[right] += 0.5
    return tallies


def _scored_trials(run_log: Mapping[str, object]) -> list[dict]:
    trials = []
    for entry in run_log.get("trials") or []:
        result = entry.get("result") or {}
        objective = result.get("objective") or {}
        rate = objective.get("objective_pass_rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            continue
        trials.append(dict(entry))
    return trials


def pick_winners(
    run_log: Mapping[str, object],
    revealed_jsonl: Path,
) -> dict[str, dict]:
    """Choose each instrument's winning entrant and its best concrete trial.

    Selection: most instrument-level blind-vote wins, tiebroken by the
    entrant's mean objective pass-rate on that instrument. Instruments with no
    votes fall back to objective rate alone. The exported trial is the winning
    entrant's best-scoring trial for the instrument (tiebreak: lowest seed,
    then rep).
    """

    tallies = instrument_vote_tallies(revealed_jsonl)
    by_instrument: dict[str, dict[str, list[dict]]] = {}
    for trial in _scored_trials(run_log):
        instrument = str(trial.get("instrument_id"))
        entrant = str(trial.get("model_id"))
        by_instrument.setdefault(instrument, {}).setdefault(entrant, []).append(trial)

    winners: dict[str, dict] = {}
    for instrument, entrants in sorted(by_instrument.items()):
        votes = tallies.get(instrument, {})

        def mean_rate(entrant: str) -> float:
            rows = entrants.get(entrant) or []
            rates = [
                float((row.get("result") or {}).get("objective", {}).get("objective_pass_rate") or 0.0)
                for row in rows
            ]
            return sum(rates) / len(rates) if rates else 0.0

        entrant = max(
            entrants,
            key=lambda name: (votes.get(name, 0.0), mean_rate(name), name),
        )
        trial = max(
            entrants[entrant],
            key=lambda row: (
                float((row.get("result") or {}).get("objective", {}).get("objective_pass_rate") or 0.0),
                -int(row.get("seed") or 0),
                -int(row.get("rep") or 0),
            ),
        )
        winners[instrument] = {
            "entrant": entrant,
            "vote_wins": votes.get(entrant, 0.0),
            "objective_rate": round(mean_rate(entrant), 6),
            "trial": trial,
        }
    return winners


def export_winners(
    *,
    run_log: Mapping[str, object],
    run_dir: Path,
    registry: Mapping[str, object],
    instruments_root: Path,
    force: bool = False,
) -> list[dict]:
    """Export each instrument's winning model into its build repo (#603).

    Copies the winning .scad/.stl/.png plus a colored .glb into
    ``<instruments_root>/<repo_path>/arena/<run_id>/`` with a provenance
    sidecar and a README marking the model as arena-generated (never a
    measured master). One-way flow only: nothing is read back from the
    instrument repo. Committing the export stays a human decision.
    """

    run_dir = Path(run_dir)
    instruments_root = Path(instruments_root)
    repo_paths = {
        str(spec.get("id")): str(spec.get("repo_path") or "")
        for spec in registry.get("instruments") or []
    }
    winners = pick_winners(run_log, run_dir / "votes.revealed.jsonl")
    summary: list[dict] = []
    for instrument, winner in winners.items():
        repo_path = repo_paths.get(instrument)
        row = {
            "instrument_id": instrument,
            "entrant": winner["entrant"],
            "vote_wins": winner["vote_wins"],
            "objective_rate": winner["objective_rate"],
            "status": "skipped",
            "dest": None,
        }
        if not repo_path:
            row["status"] = "no repo_path in registry"
            summary.append(row)
            continue
        repo_dir = instruments_root / repo_path
        if not repo_dir.is_dir():
            row["status"] = f"instrument repo missing: {repo_dir}"
            summary.append(row)
            continue

        trial = winner["trial"]
        result = trial.get("result") or {}
        artifacts = result.get("artifacts") or {}
        scad_src = Path(str((result.get("gen") or {}).get("scad_path") or ""))
        stl_src = Path(str(artifacts.get("stl_path") or ""))
        png_src = Path(str(artifacts.get("png_path") or ""))
        if not scad_src.exists() or not stl_src.exists():
            row["status"] = "winning trial artifacts missing on disk"
            summary.append(row)
            continue

        dest = repo_dir / "arena" / run_dir.name
        if dest.exists() and any(dest.iterdir()) and not force:
            row["status"] = f"exists (use --force): {dest}"
            row["dest"] = dest.as_posix()
            summary.append(row)
            continue
        dest.mkdir(parents=True, exist_ok=True)

        shutil.copy2(scad_src, dest / f"{instrument}-arena-winner.scad")
        shutil.copy2(stl_src, dest / f"{instrument}-arena-winner.stl")
        if png_src.exists():
            shutil.copy2(png_src, dest / f"{instrument}-arena-winner.png")
        glb = ensure_glb(stl_src)
        if glb is not None:
            shutil.copy2(glb, dest / f"{instrument}-arena-winner.glb")

        provenance = {
            "schema": "makerbench-code-cad-arena-winner-v1",
            "run_id": run_dir.name,
            "instrument_id": instrument,
            "entrant": winner["entrant"],
            "trial_id": trial.get("trial_id"),
            "seed": trial.get("seed"),
            "rep": trial.get("rep"),
            "vote_wins": winner["vote_wins"],
            "objective": result.get("objective"),
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generated": True,
        }
        (dest / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (dest / "README.md").write_text(
            f"# Arena winner: {instrument} ({run_dir.name})\n\n"
            f"**Generated model** — the winning Code-CAD Arena candidate for this\n"
            f"instrument (entrant `{winner['entrant']}`, blind-vote wins "
            f"{winner['vote_wins']}, objective pass-rate {winner['objective_rate']}).\n\n"
            "This is an AI-generated arena artifact, NOT a measured master or a\n"
            "validated build packet. See `provenance.json` for the full trial\n"
            "record. Exported by `makerbench arena export-winners`.\n",
            encoding="utf-8",
        )
        row["status"] = "exported"
        row["dest"] = dest.as_posix()
        summary.append(row)
    return summary
