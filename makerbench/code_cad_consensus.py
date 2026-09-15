"""Offline consensus best-of-N selector tier, ``consensus@N`` (#796).

Given N >= 3 candidates that were already generated and scored for the same
task, entrant and tier, pick the one whose shape agrees most with the others:
the lowest mean symmetric Chamfer distance to every other candidate. This is
selection over existing artifacts only; nothing is generated, compiled or
executed.

Alignment. Each candidate's sampled points are translated so its axis-aligned
bounding-box centre sits at the origin, and nothing else:

* **Translation is removed** because placement is an authoring convention
  (``center=true`` versus a corner at the origin) that says nothing about the
  design, and it would otherwise dominate the distance.
* **Scale is kept** because tasks are specified in millimetres. A candidate at
  the wrong scale is a wrong design and should look like an outlier.
* **Rotation is kept** because tasks declare their axes. Rotational fitting
  (ICP or PCA) would hide a mis-oriented part, and it adds local-minimum
  nondeterminism the selector must not have.

Ties. Mean distances are rounded to ``TIE_DECIMALS`` decimal places (1e-9 mm)
before comparison, so floating-point noise cannot reorder candidates. Equal
means then break by candidate id, lexicographically.

Reporting. The result is its own tier, ``consensus@N``. It is never merged into
blind Elo, vote queues, ``vote_pages/`` or single-shot series; see
:data:`CONSENSUS_TIER_PREFIX` and :func:`is_consensus_tier`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from . import measure
from .code_cad_arena_runner import CONSENSUS_TIER_PREFIX, is_consensus_row

SCHEMA = "makerbench-code-cad-consensus-v1"
SIDECAR_NAME = "consensus.json"
MIN_CANDIDATES = 3
DEFAULT_SAMPLES = 2048
DEFAULT_SEED = 0
TIE_DECIMALS = 9
UNRECORDED_TIER = "unrecorded"


class ConsensusError(ValueError):
    """The candidate set cannot be used for consensus selection."""


def consensus_tier_name(n: int) -> str:
    return f"{CONSENSUS_TIER_PREFIX}{int(n)}"


def is_consensus_tier(tier: object) -> bool:
    return isinstance(tier, str) and tier.startswith(CONSENSUS_TIER_PREFIX)


def sample_points(mesh: trimesh.Trimesh, *, samples: int = DEFAULT_SAMPLES,
                  seed: int = DEFAULT_SEED) -> np.ndarray:
    """Seeded surface samples, translated so the bbox centre is the origin."""
    problem = measure.mesh_problem(mesh)
    if problem:
        raise ConsensusError(problem)
    points, _ = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    centre = (mesh.bounds[0] + mesh.bounds[1]) / 2.0
    return np.asarray(points, dtype=float) - centre


def chamfer_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Symmetric Chamfer distance: mean nearest-neighbour distance a->b plus b->a, halved."""
    d_ab, _ = cKDTree(b).query(a)
    d_ba, _ = cKDTree(a).query(b)
    return float((np.mean(d_ab) + np.mean(d_ba)) / 2.0)


@dataclass(frozen=True)
class ConsensusSelection:
    tier: str
    n: int
    selected: str
    mean_chamfer_mm: dict[str, float]
    ranking: list[str]
    pairwise_chamfer_mm: dict[str, dict[str, float]]
    method: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_consensus(points: Mapping[str, np.ndarray]) -> ConsensusSelection:
    """Pick the candidate with the lowest mean Chamfer distance to the others."""
    ids = sorted(points)
    if len(ids) < MIN_CANDIDATES:
        raise ConsensusError(
            f"consensus@N needs at least {MIN_CANDIDATES} candidates for the same task, "
            f"entrant and tier; got {len(ids)}"
        )
    pairwise: dict[str, dict[str, float]] = {cid: {} for cid in ids}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            distance = chamfer_distance(points[a], points[b])
            pairwise[a][b] = distance
            pairwise[b][a] = distance
    means = {cid: float(np.mean(list(pairwise[cid].values()))) for cid in ids}
    ranking = sorted(ids, key=lambda cid: (round(means[cid], TIE_DECIMALS), cid))
    return ConsensusSelection(
        tier=consensus_tier_name(len(ids)),
        n=len(ids),
        selected=ranking[0],
        mean_chamfer_mm=means,
        ranking=ranking,
        pairwise_chamfer_mm=pairwise,
        method=(
            "lowest mean symmetric Chamfer distance over seeded surface samples; "
            "bbox-centre translation removed, scale and rotation kept; "
            f"ties rounded to 1e-{TIE_DECIMALS} mm then broken by candidate id"
        ),
    )


def select_consensus_meshes(meshes: Mapping[str, trimesh.Trimesh], *,
                            samples: int = DEFAULT_SAMPLES,
                            seed: int = DEFAULT_SEED) -> ConsensusSelection:
    if len(meshes) < MIN_CANDIDATES:
        return select_consensus({cid: np.empty((0, 3)) for cid in meshes})
    return select_consensus(
        {cid: sample_points(mesh, samples=samples, seed=seed) for cid, mesh in meshes.items()}
    )


def exclude_consensus_rows(rows: Sequence[Mapping[str, Any]], *,
                           tier_key: str = "tier") -> list[Mapping[str, Any]]:
    """Drop consensus-tier rows; the filter every blind or single-shot feed applies."""
    return [row for row in rows if not is_consensus_tier(row.get(tier_key))]


def _source_tier(entry: Mapping[str, Any]) -> str:
    result = entry.get("result") or {}
    meta = entry.get("meta") or {}
    return str(result.get("context_tier") or meta.get("context_tier") or UNRECORDED_TIER)


def _eligible_stl(entry: Mapping[str, Any], run_dir: Path) -> tuple[Path | None, str | None]:
    """The candidate's STL if it is a scored, rendered trial inside ``run_dir``."""
    if str(entry.get("status")) != "scored":
        return None, f"status is {entry.get('status')!r}, not 'scored'"
    result = entry.get("result") or {}
    if not result.get("render_ok") or not isinstance(result.get("objective"), Mapping):
        return None, "no rendered, scored result"
    raw = (result.get("artifacts") or {}).get("stl_path")
    if not raw:
        return None, "no stl_path"
    path = Path(str(raw))
    path = (path if path.is_absolute() else run_dir / path).resolve()
    if not path.is_relative_to(run_dir):
        return None, "stl_path is outside the run directory"
    if not path.is_file():
        return None, "stl_path does not exist"
    return path, None


def build_consensus_report(run_dir: str | Path, *, samples: int = DEFAULT_SAMPLES,
                           seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Consensus selections for every (instrument, entrant, tier) group in a run.

    Reads ``run_log.json`` only; it never writes to it. Groups with fewer than
    ``MIN_CANDIDATES`` eligible candidates are listed under ``refused``.
    """
    run_dir = Path(run_dir).resolve(strict=True)
    run_log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    skipped = []
    for entry in run_log.get("trials") or []:
        if is_consensus_row(entry):
            continue
        key = (str(entry.get("instrument_id")), str(entry.get("model_id")), _source_tier(entry))
        stl, reason = _eligible_stl(entry, run_dir)
        if stl is None:
            skipped.append({"trial_id": entry.get("trial_id"), "reason": reason})
            continue
        groups.setdefault(key, []).append({"entry": entry, "stl": stl})

    selections, refused = [], []
    for (instrument_id, model_id, source_tier), members in sorted(groups.items()):
        base = {"instrument_id": instrument_id, "model_id": model_id,
                "source_tier": source_tier, "n": len(members)}
        by_id = {str(m["entry"]["trial_id"]): m for m in members}
        if len(by_id) < MIN_CANDIDATES:
            refused.append({**base, "reason": (
                f"needs at least {MIN_CANDIDATES} scored candidates for the same task, "
                f"entrant and tier; found {len(by_id)}")})
            continue
        try:
            meshes = {cid: measure.load_mesh(m["stl"]) for cid, m in by_id.items()}
            selection = select_consensus_meshes(meshes, samples=samples, seed=seed)
        except (ConsensusError, ValueError, OSError) as exc:
            refused.append({**base, "reason": f"unmeasurable candidate: {exc}"})
            continue
        rates = {cid: float(m["entry"]["result"]["objective"].get("objective_pass_rate") or 0.0)
                 for cid, m in by_id.items()}
        selections.append({
            **base,
            "tier": selection.tier,
            "candidates": sorted(by_id),
            "selected_trial_id": selection.selected,
            "selected_objective_pass_rate": round(rates[selection.selected], 6),
            "single_shot_mean_objective_pass_rate": round(sum(rates.values()) / len(rates), 6),
            "ranking": selection.ranking,
            "mean_chamfer_mm": {k: round(v, 6) for k, v in selection.mean_chamfer_mm.items()},
        })
    return {
        "schema": SCHEMA,
        "method": (
            "lowest mean symmetric Chamfer distance to the other candidates over seeded "
            "surface samples; bbox-centre translation removed, scale and rotation kept; "
            f"ties rounded to 1e-{TIE_DECIMALS} mm then broken by trial id"),
        "reporting": "separate tier; never merged into run_log.json, blind votes, Elo or "
                     "the single-shot objective scoreline",
        "samples": samples,
        "seed": seed,
        "selections": selections,
        "refused": refused,
        "skipped": skipped,
    }
