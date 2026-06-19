"""
arena.elo — Pure-Python pairwise Elo rating engine (#425).

Design
------
The engine is entrant-generic: it rates any ``entrant_id`` string, whether that
refers to an AI model, a human author, or a human+AI pair.  This means stories
#428-430 (timed human tracks) simply register new entrant_ids and the leaderboard
renders them alongside models automatically.

Elo mechanics
-------------
Standard Elo with configurable K-factor.  For the arena context we use:

* K = 32   (standard for new/few-game entrants)
* Starting rating = 1200 (Lichess / chess default — familiar to the community)
* Ties split the expected gain evenly between both entrants

Sampling strategy (avoids all-pairs blowup)
--------------------------------------------
With M models and N specs, naive all-pairs = M*(M-1)/2 comparisons per spec.
For M=6 that's 15 pairs; for M=10 that's 45.  The recommended strategy (see
``SamplingStrategy`` enum) is **swiss-style**: rank entrants by current rating,
pair adjacent-ranked entrants each round.  This:
* Concentrates comparisons where the ranking is uncertain
* Gives O(M) matches per round rather than O(M²)
* Converges to a stable ordering faster than random pairing

The sampling logic lives in ``suggest_next_pairs()``; the Elo updates themselves
are agnostic to sampling — the engine just processes (a, b, outcome) tuples.

Single-voter caveat
-------------------
Epic #421 notes that Tony is the only voter: results are directional signals, not
population claims.  This is baked into the ``Leaderboard.caveat`` field so it
appears in any serialised output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from arena.models import MatchResult, VoteOutcome


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_K: float = 32.0
DEFAULT_STARTING_RATING: float = 1200.0
SINGLE_VOTER_CAVEAT = (
    "Single-voter study (n=1). Ratings are directional signals only, "
    "not population-level claims. See epic #421 for full caveat."
)


# ---------------------------------------------------------------------------
# Rating record
# ---------------------------------------------------------------------------


@dataclass
class EloRating:
    """Current Elo state for one entrant.

    Attributes
    ----------
    entrant_id:
        Stable identifier (model slug, ``"human:<handle>"``, etc.).
    rating:
        Current Elo rating (starts at ``DEFAULT_STARTING_RATING``).
    wins:
        Number of pairwise comparisons won.
    losses:
        Number of pairwise comparisons lost.
    ties:
        Number of tied comparisons.
    matches_played:
        Total comparisons (wins + losses + ties).
    """

    entrant_id: str
    rating: float = DEFAULT_STARTING_RATING
    wins: int = 0
    losses: int = 0
    ties: int = 0

    @property
    def matches_played(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def win_rate(self) -> Optional[float]:
        """Win fraction [0, 1]; None if no matches played."""
        if self.matches_played == 0:
            return None
        return self.wins / self.matches_played

    def __repr__(self) -> str:
        return (
            f"EloRating(id={self.entrant_id!r}, rating={self.rating:.1f}, "
            f"W={self.wins} L={self.losses} T={self.ties})"
        )


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@dataclass
class Leaderboard:
    """Ranked snapshot of all entrant ratings.

    Attributes
    ----------
    ratings:
        Ordered list of ``EloRating`` objects, highest rating first.
    total_matches:
        Total pairwise match results processed to produce this leaderboard.
    caveat:
        Methodological warning; always populated with the single-voter note.
    """

    ratings: list[EloRating]
    total_matches: int = 0
    caveat: str = SINGLE_VOTER_CAVEAT

    def __str__(self) -> str:
        lines = ["# Code-CAD Arena Leaderboard", ""]
        lines.append(f"{'Rank':<5} {'Entrant':<30} {'Rating':>8} {'W':>5} {'L':>5} {'T':>5} {'Played':>8}")
        lines.append("-" * 65)
        for rank, r in enumerate(self.ratings, 1):
            lines.append(
                f"{rank:<5} {r.entrant_id:<30} {r.rating:>8.1f} "
                f"{r.wins:>5} {r.losses:>5} {r.ties:>5} {r.matches_played:>8}"
            )
        lines.append("")
        lines.append(f"Total matches processed: {self.total_matches}")
        lines.append(f"⚠ {self.caveat}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sampling strategies
# ---------------------------------------------------------------------------


class SamplingStrategy(str, Enum):
    """Strategy for selecting which pairs to compare next.

    ALL_PAIRS:
        Every possible pair in every round.  O(M²) per spec — fine for M ≤ 5,
        degrades at larger scale.
    SWISS:
        Pair adjacent-rated entrants each round.  O(M) per round; recommended
        default.  Concentrates matches at the uncertain rating boundaries.
    RANDOM:
        Sample pairs uniformly at random.  Simple baseline; converges slower
        than swiss for the same match budget.
    """

    ALL_PAIRS = "all_pairs"
    SWISS = "swiss"
    RANDOM = "random"


# ---------------------------------------------------------------------------
# Elo engine
# ---------------------------------------------------------------------------


class EloEngine:
    """Pairwise Elo rating engine for the Code-CAD Arena.

    Usage
    -----
    .. code-block:: python

        engine = EloEngine()
        engine.register_entrant("gpt-4o")
        engine.register_entrant("claude-sonnet")
        engine.register_entrant("gemini-2.5-pro")

        # After each blind vote:
        match = MatchResult(
            spec_id="dulcimer-3string-v1",
            seed=42,
            entrant_a_id="gpt-4o",
            entrant_b_id="claude-sonnet",
            outcome=VoteOutcome.B_WINS,
        )
        engine.process_match(match)

        print(engine.leaderboard())

    Parameters
    ----------
    k_factor:
        Elo K-factor (default 32).  Higher K = faster rating changes.
    starting_rating:
        Initial rating for newly registered entrants (default 1200).
    """

    def __init__(
        self,
        k_factor: float = DEFAULT_K,
        starting_rating: float = DEFAULT_STARTING_RATING,
    ) -> None:
        self._k = k_factor
        self._starting = starting_rating
        self._ratings: dict[str, EloRating] = {}
        self._match_log: list[MatchResult] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_entrant(self, entrant_id: str) -> EloRating:
        """Ensure *entrant_id* has a rating record; return it.

        Idempotent: calling this multiple times for the same id is safe.
        """
        if entrant_id not in self._ratings:
            self._ratings[entrant_id] = EloRating(
                entrant_id=entrant_id, rating=self._starting
            )
        return self._ratings[entrant_id]

    def get_rating(self, entrant_id: str) -> Optional[EloRating]:
        """Return the current rating for *entrant_id*, or None if unknown."""
        return self._ratings.get(entrant_id)

    # ------------------------------------------------------------------
    # Core Elo update
    # ------------------------------------------------------------------

    @staticmethod
    def _expected_score(rating_a: float, rating_b: float) -> float:
        """Expected score for A in a match against B (standard Elo formula)."""
        return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))

    def _update_pair(
        self,
        rating_a: EloRating,
        rating_b: EloRating,
        outcome: VoteOutcome,
    ) -> None:
        """Apply one Elo update in place."""
        ea = self._expected_score(rating_a.rating, rating_b.rating)
        eb = 1.0 - ea

        if outcome == VoteOutcome.A_WINS:
            sa, sb = 1.0, 0.0
        elif outcome == VoteOutcome.B_WINS:
            sa, sb = 0.0, 1.0
        else:  # TIE
            sa, sb = 0.5, 0.5

        rating_a.rating += self._k * (sa - ea)
        rating_b.rating += self._k * (sb - eb)

        # Update win/loss/tie counters
        if outcome == VoteOutcome.A_WINS:
            rating_a.wins += 1
            rating_b.losses += 1
        elif outcome == VoteOutcome.B_WINS:
            rating_b.wins += 1
            rating_a.losses += 1
        else:
            rating_a.ties += 1
            rating_b.ties += 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_match(self, match: MatchResult) -> None:
        """Update ratings from a completed blind-vote match.

        Auto-registers any entrant_id not yet seen.  Appends the match to the
        internal log so the full history is available via ``match_log``.

        Parameters
        ----------
        match:
            A completed ``MatchResult`` with a non-None outcome.
        """
        r_a = self.register_entrant(match.entrant_a_id)
        r_b = self.register_entrant(match.entrant_b_id)
        self._update_pair(r_a, r_b, match.outcome)
        self._match_log.append(match)

    def process_matches(self, matches: list[MatchResult]) -> None:
        """Convenience batch wrapper around ``process_match``."""
        for m in matches:
            self.process_match(m)

    def leaderboard(self) -> Leaderboard:
        """Return the current leaderboard (sorted by rating, highest first)."""
        ranked = sorted(
            self._ratings.values(), key=lambda r: r.rating, reverse=True
        )
        return Leaderboard(
            ratings=ranked,
            total_matches=len(self._match_log),
        )

    @property
    def match_log(self) -> list[MatchResult]:
        """Read-only view of all matches processed (do not mutate)."""
        return list(self._match_log)

    # ------------------------------------------------------------------
    # Sampling helpers
    # ------------------------------------------------------------------

    def suggest_next_pairs(
        self,
        strategy: SamplingStrategy = SamplingStrategy.SWISS,
        n_pairs: Optional[int] = None,
    ) -> list[tuple[str, str]]:
        """Suggest which pairs to compare next.

        Parameters
        ----------
        strategy:
            Pairing strategy (default: SWISS — pairs adjacent-ranked entrants).
        n_pairs:
            Max number of pairs to suggest.  None = return all pairs the strategy
            would produce for one round.

        Returns
        -------
        list of (entrant_a_id, entrant_b_id) tuples, deduplicated so (a,b) and
        (b,a) are treated as the same pair.
        """
        if len(self._ratings) < 2:
            return []

        ranked_ids = [
            r.entrant_id
            for r in sorted(
                self._ratings.values(), key=lambda r: r.rating, reverse=True
            )
        ]

        if strategy == SamplingStrategy.ALL_PAIRS:
            pairs = [
                (ranked_ids[i], ranked_ids[j])
                for i in range(len(ranked_ids))
                for j in range(i + 1, len(ranked_ids))
            ]
        elif strategy == SamplingStrategy.SWISS:
            # Pair position 0&1, 2&3, 4&5, … (adjacent-ranked)
            pairs = [
                (ranked_ids[i], ranked_ids[i + 1])
                for i in range(0, len(ranked_ids) - 1, 2)
            ]
            # If odd number, add the last unpaired entrant against position 0
            if len(ranked_ids) % 2 == 1:
                pairs.append((ranked_ids[0], ranked_ids[-1]))
        elif strategy == SamplingStrategy.RANDOM:
            import random  # noqa: PLC0415
            ids = list(ranked_ids)
            random.shuffle(ids)
            pairs = [
                (ids[i], ids[i + 1])
                for i in range(0, len(ids) - 1, 2)
            ]
        else:
            raise ValueError(f"Unknown strategy: {strategy!r}")

        if n_pairs is not None:
            pairs = pairs[:n_pairs]
        return pairs
