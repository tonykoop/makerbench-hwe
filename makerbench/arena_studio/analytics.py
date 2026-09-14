"""Agreement analytics for MakerBench Arena Studio (Issue #699, Epic #694).

This module composes the existing arena math (:mod:`makerbench.code_cad_arena`,
:mod:`makerbench.code_cad_arena_runner`, :mod:`makerbench.code_cad_agreement`)
rather than reimplementing it — it never changes an Elo rating or a Spearman
rho that those modules already publish to ``site/``. It only adds three things
on top: seeded bootstrap confidence intervals on Elo, an explicit small-sample
caveat on rho, and a per-instrument-family slice of the same math.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Mapping, Optional

from .. import code_cad_agreement as agreement_mod
from .. import code_cad_arena as arena_elo
from .. import code_cad_arena_runner as arena_runner
from ..cli_arena import drop_unrated_entrants

SCHEMA = "makerbench-arena-studio-analytics-v1"

# Below this many paired observations, a Spearman rho is dominated by sampling
# noise rather than signal; flag it instead of presenting it bare.
SMALL_SAMPLE_THRESHOLD = 8
DEFAULT_BOOTSTRAP_RESAMPLES = 1000
DEFAULT_CONFIDENCE = 0.95


def bootstrap_elo_ci(
    votes: list[arena_elo.Vote],
    entrants: list[str],
    *,
    config: Optional[arena_elo.EloConfig] = None,
    seed: int = 0,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[str, dict[str, object]]:
    """Seeded, reproducible bootstrap confidence intervals on Elo ratings.

    Resamples ``votes`` with replacement ``n_resamples`` times using a
    dedicated ``random.Random(seed)`` instance (never the global RNG, so two
    calls with the same seed and votes are byte-identical), rebuilds the Elo
    leaderboard on each resample, and reports the percentile interval per
    entrant. An entrant that never plays in any resample gets ``None`` bounds
    rather than a misleadingly tight interval pinned at the initial rating.
    """

    result: dict[str, dict[str, object]] = {
        entrant: {"lo": None, "hi": None, "n_resamples": 0} for entrant in entrants
    }
    if not votes or n_resamples <= 0:
        return result

    rng = random.Random(seed)
    n = len(votes)
    samples: dict[str, list[float]] = {entrant: [] for entrant in entrants}
    played: dict[str, int] = {entrant: 0 for entrant in entrants}

    for _ in range(n_resamples):
        resample = [votes[rng.randrange(n)] for _ in range(n)]
        payload = arena_elo.build_elo_leaderboard(resample, entrants=entrants, config=config)
        for row in payload.get("leaderboard") or []:
            entrant = row["entrant"]
            samples.setdefault(entrant, []).append(row["rating"])
            if row.get("games"):
                played[entrant] = played.get(entrant, 0) + 1

    lo_q = (1.0 - confidence) / 2.0
    hi_q = 1.0 - lo_q

    for entrant, ratings in samples.items():
        if not ratings or not played.get(entrant):
            result[entrant] = {"lo": None, "hi": None, "n_resamples": len(ratings)}
            continue
        ordered = sorted(ratings)
        result[entrant] = {
            "lo": round(_percentile(ordered, lo_q), 1),
            "hi": round(_percentile(ordered, hi_q), 1),
            "n_resamples": len(ratings),
        }
    return result


def _percentile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    idx = q * (len(ordered) - 1)
    lo_idx = int(idx)
    hi_idx = min(lo_idx + 1, len(ordered) - 1)
    frac = idx - lo_idx
    return ordered[lo_idx] + (ordered[hi_idx] - ordered[lo_idx]) * frac


def leaderboard_with_ci(
    run_dir: Path,
    run_log: Mapping[str, object],
    *,
    seed: int = 0,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict:
    """The published Elo leaderboard, unchanged, plus a bootstrap CI per row."""

    run_dir = Path(run_dir)
    votes = arena_runner.votes_to_elo_votes(run_dir / "votes.revealed.jsonl")
    entrants = list((run_log.get("config") or {}).get("model_ids") or [])
    payload = drop_unrated_entrants(arena_elo.build_elo_leaderboard(votes, entrants=entrants))
    ci_by_entrant = bootstrap_elo_ci(
        votes, entrants, seed=seed, n_resamples=n_resamples, confidence=confidence
    )
    for row in payload.get("leaderboard") or []:
        ci = ci_by_entrant.get(row["entrant"], {"lo": None, "hi": None})
        row["ci_low"] = ci.get("lo")
        row["ci_high"] = ci.get("hi")
    payload["ci_method"] = {
        "kind": "bootstrap_percentile",
        "seed": seed,
        "n_resamples": n_resamples,
        "confidence": confidence,
    }
    return payload


def agreement_with_caveats(rows) -> dict:
    """:func:`code_cad_agreement.build_agreement_summary`, unchanged, plus an
    explicit small-sample caveat on every pairwise rho backed by fewer than
    :data:`SMALL_SAMPLE_THRESHOLD` shared entrants.
    """

    summary = agreement_mod.build_agreement_summary(rows)
    _annotate_small_sample(summary.get("agreement"))
    for pair in (summary.get("matrix") or {}).values():
        _annotate_small_sample(pair)
    return summary


def _annotate_small_sample(pair: Optional[dict]) -> None:
    if not pair:
        return
    n = pair.get("n")
    small = bool(n is not None and n < SMALL_SAMPLE_THRESHOLD)
    pair["small_sample"] = small
    if small:
        pair["caveat"] = (
            f"n={n} < {SMALL_SAMPLE_THRESHOLD}: rho is not meaningful at this sample size."
        )


def find_outliers(agreement_summary: Mapping[str, object], threshold: float = 2.0) -> list[dict]:
    """Entrants whose objective-vs-subjective rank disagrees by at least
    ``threshold`` places — the Agreement Studio's outlier call-outs (#699).
    """

    rows = agreement_summary.get("rankings") or []
    return [
        dict(row)
        for row in rows
        if row.get("rank_delta") is not None and abs(row["rank_delta"]) >= threshold
    ]


def per_family_breakdown(
    run_dir: Path,
    run_log: Mapping[str, object],
    registry_tasks: list[Mapping[str, object]],
) -> dict[str, dict]:
    """Split the leaderboard and agreement summary out by instrument family.

    Votes and objective trials are filtered down to the instrument ids that
    belong to each registry family *before* recomputing the (unchanged) Elo
    and Spearman math on that slice — this is a slice of the real numbers,
    never a re-weighted or re-derived global figure.
    """

    run_dir = Path(run_dir)
    family_by_task = {
        str(t.get("id")): str(t.get("family") or "unknown") for t in registry_tasks
    }
    entrants = list((run_log.get("config") or {}).get("model_ids") or [])
    all_votes = arena_runner.votes_to_elo_votes(run_dir / "votes.revealed.jsonl")

    trials_by_family: dict[str, list] = {}
    for trial in run_log.get("trials") or []:
        family = family_by_task.get(str(trial.get("instrument_id")), "unknown")
        trials_by_family.setdefault(family, []).append(trial)

    out: dict[str, dict] = {}
    for family, trials in trials_by_family.items():
        family_task_ids = {tid for tid, fam in family_by_task.items() if fam == family}
        family_votes = [v for v in all_votes if v.instrument_id in family_task_ids]
        if not family_votes:
            continue

        elo_payload = drop_unrated_entrants(
            arena_elo.build_elo_leaderboard(family_votes, entrants=entrants)
        )
        family_log = {"config": run_log.get("config"), "trials": trials}
        family_scoreline = arena_runner.collect_objective_scoreline(family_log)
        rows = arena_runner.build_agreement_rows(elo_payload, family_scoreline)

        out[family] = {
            "leaderboard": elo_payload.get("leaderboard") or [],
            "unrated_entrants": elo_payload.get("unrated_entrants") or [],
            "agreement": agreement_with_caveats(rows),
            "n_trials": len(trials),
        }
    return out
