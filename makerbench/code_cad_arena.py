"""Pairwise Elo utilities for the Code-CAD A/B Arena (#425).

The arena's objective is intentionally narrow: blind human votes arrive as
pairwise outcomes between two rendered candidates, and this module turns those
votes into a deterministic per-model leaderboard. It does not render, grade, or
read source CAD; upstream stories own generation and objective scoring.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

SCHEMA = "makerbench-code-cad-arena-elo-v1"
DEFAULT_RATING = 1000.0
DEFAULT_K_FACTOR = 32.0

Outcome = Literal["left", "right", "tie"]


@dataclass(frozen=True)
class PairwiseVote:
    """A blind vote between two model candidates for the same trial."""

    trial_id: str
    left_model: str
    right_model: str
    winner: Outcome
    voter_id: str = "tony"
    spec_id: str | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        if not self.trial_id.strip():
            raise ValueError("trial_id must be non-empty")
        if not self.left_model.strip() or not self.right_model.strip():
            raise ValueError("model ids must be non-empty")
        if self.left_model == self.right_model:
            raise ValueError("left_model and right_model must differ")
        if self.winner not in ("left", "right", "tie"):
            raise ValueError("winner must be 'left', 'right', or 'tie'")


@dataclass(frozen=True)
class RatingState:
    """Aggregate arena state for one model."""

    model: str
    rating: float = DEFAULT_RATING
    votes: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    last_delta: float = 0.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """Return model A's Elo expected score against model B."""

    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _score_for(vote: PairwiseVote) -> tuple[float, float]:
    if vote.winner == "left":
        return 1.0, 0.0
    if vote.winner == "right":
        return 0.0, 1.0
    return 0.5, 0.5


def _bump(state: RatingState, *, delta: float, score: float) -> RatingState:
    return RatingState(
        model=state.model,
        rating=state.rating + delta,
        votes=state.votes + 1,
        wins=state.wins + int(score == 1.0),
        losses=state.losses + int(score == 0.0),
        ties=state.ties + int(score == 0.5),
        last_delta=delta,
    )


def apply_vote(
    ratings: Mapping[str, RatingState],
    vote: PairwiseVote,
    *,
    initial_rating: float = DEFAULT_RATING,
    k_factor: float = DEFAULT_K_FACTOR,
) -> dict[str, RatingState]:
    """Return updated rating states after one pairwise vote.

    The input mapping is not mutated, which keeps tests and future resumable run
    logs straightforward.
    """

    if k_factor <= 0:
        raise ValueError("k_factor must be positive")

    out = dict(ratings)
    left = out.get(vote.left_model, RatingState(vote.left_model, initial_rating))
    right = out.get(vote.right_model, RatingState(vote.right_model, initial_rating))

    left_score, right_score = _score_for(vote)
    left_expected = expected_score(left.rating, right.rating)
    right_expected = 1.0 - left_expected
    left_delta = k_factor * (left_score - left_expected)
    right_delta = k_factor * (right_score - right_expected)

    out[vote.left_model] = _bump(left, delta=left_delta, score=left_score)
    out[vote.right_model] = _bump(right, delta=right_delta, score=right_score)
    return out


def build_elo_leaderboard(
    votes: Iterable[PairwiseVote],
    *,
    initial_rating: float = DEFAULT_RATING,
    k_factor: float = DEFAULT_K_FACTOR,
) -> list[dict]:
    """Aggregate votes into a sorted per-model leaderboard."""

    states: dict[str, RatingState] = {}
    for vote in votes:
        states = apply_vote(
            states,
            vote,
            initial_rating=initial_rating,
            k_factor=k_factor,
        )

    rows = [
        {
            "schema": SCHEMA,
            "rank": 0,
            "model": state.model,
            "rating": round(state.rating, 1),
            "votes": state.votes,
            "wins": state.wins,
            "losses": state.losses,
            "ties": state.ties,
            "last_delta": round(state.last_delta, 2),
        }
        for state in states.values()
    ]
    rows.sort(key=lambda row: (-row["rating"], -row["wins"], row["losses"], row["model"]))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def render_markdown_leaderboard(rows: Sequence[Mapping[str, object]]) -> str:
    """Render a compact leaderboard view for docs, PRs, or static-site ingestion."""

    lines = [
        "| Rank | Model | Elo | Votes | W-L-T | Last delta |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        record = dict(row)
        lines.append(
            "| {rank} | `{model}` | {rating:.1f} | {votes} | "
            "{wins}-{losses}-{ties} | {last_delta:+.2f} |".format(
                rank=int(record["rank"]),
                model=str(record["model"]),
                rating=float(record["rating"]),
                votes=int(record["votes"]),
                wins=int(record["wins"]),
                losses=int(record["losses"]),
                ties=int(record["ties"]),
                last_delta=float(record["last_delta"]),
            )
        )
    return "\n".join(lines)


def bounded_pair_sample(
    models: Sequence[str],
    *,
    seed: int = 0,
    max_pairs_per_model: int = 3,
    prior_pair_counts: Mapping[tuple[str, str], int] | None = None,
) -> list[tuple[str, str]]:
    """Pick a deterministic bounded set of pairings without all-pairs blowup.

    The strategy shuffles model ids with a public seed, scores candidate pairs by
    how rarely they have already been seen, and greedily accepts pairs while each
    model stays under ``max_pairs_per_model`` in this batch. For M models this is
    O(M^2) to rank candidates but emits at most ``M * max_pairs_per_model / 2``
    pairs, so human voting stays linear-ish rather than all-pairs quadratic.
    """

    unique = sorted({m.strip() for m in models if m and m.strip()})
    if len(unique) < 2:
        return []
    if max_pairs_per_model < 1:
        raise ValueError("max_pairs_per_model must be at least 1")

    rng = random.Random(seed)
    shuffled = list(unique)
    rng.shuffle(shuffled)
    order = {model: idx for idx, model in enumerate(shuffled)}

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for pair, count in (prior_pair_counts or {}).items():
        if len(pair) != 2:
            continue
        a, b = sorted((pair[0], pair[1]))
        counts[(a, b)] += int(count)

    candidates: list[tuple[int, int, int, str, str]] = []
    for i, a in enumerate(unique):
        for b in unique[i + 1 :]:
            pair = tuple(sorted((a, b)))
            spacing = abs(order[a] - order[b])
            candidates.append((counts[pair], spacing, min(order[a], order[b]), a, b))
    candidates.sort()

    used = defaultdict(int)
    selected: list[tuple[str, str]] = []
    for _, _, _, a, b in candidates:
        if used[a] >= max_pairs_per_model or used[b] >= max_pairs_per_model:
            continue
        selected.append((a, b))
        used[a] += 1
        used[b] += 1
    return selected
