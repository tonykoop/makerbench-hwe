"""
Tests for arena.elo — Elo update correctness, leaderboard ordering, and sampling.

All tests are pure Python, offline, no mocks needed.
"""

from __future__ import annotations

import math

import pytest

from arena.elo import (
    DEFAULT_STARTING_RATING,
    EloEngine,
    EloRating,
    Leaderboard,
    SamplingStrategy,
)
from arena.models import MatchResult, VoteOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_match(
    a: str,
    b: str,
    outcome: VoteOutcome,
    spec_id: str = "dulcimer-3string-v1",
    seed: int = 42,
) -> MatchResult:
    return MatchResult(
        spec_id=spec_id,
        seed=seed,
        entrant_a_id=a,
        entrant_b_id=b,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_creates_default_rating(self):
        engine = EloEngine()
        r = engine.register_entrant("gpt-4o")
        assert r.rating == DEFAULT_STARTING_RATING
        assert r.wins == 0
        assert r.losses == 0
        assert r.ties == 0

    def test_register_idempotent(self):
        engine = EloEngine()
        r1 = engine.register_entrant("mock")
        r2 = engine.register_entrant("mock")
        assert r1 is r2

    def test_get_rating_returns_none_for_unknown(self):
        engine = EloEngine()
        assert engine.get_rating("nobody") is None

    def test_get_rating_returns_record_after_registration(self):
        engine = EloEngine()
        engine.register_entrant("claude-sonnet")
        r = engine.get_rating("claude-sonnet")
        assert r is not None
        assert r.entrant_id == "claude-sonnet"


# ---------------------------------------------------------------------------
# Elo update mechanics
# ---------------------------------------------------------------------------


class TestEloUpdates:
    def test_winner_rating_increases(self):
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        rating_a_before = engine.get_rating("A").rating

        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))

        assert engine.get_rating("A").rating > rating_a_before

    def test_loser_rating_decreases(self):
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        rating_b_before = engine.get_rating("B").rating

        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))

        assert engine.get_rating("B").rating < rating_b_before

    def test_rating_change_is_symmetric_at_equal_ratings(self):
        """Equal-rated entrants: winner gains exactly as much as loser loses."""
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")

        before_a = engine.get_rating("A").rating
        before_b = engine.get_rating("B").rating

        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))

        gain_a = engine.get_rating("A").rating - before_a
        loss_b = before_b - engine.get_rating("B").rating

        assert abs(gain_a - loss_b) < 1e-9

    def test_tie_changes_no_rating_at_equal_ratings(self):
        """Tie between equal-rated entrants: ratings unchanged."""
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        before_a = engine.get_rating("A").rating
        before_b = engine.get_rating("B").rating

        engine.process_match(make_match("A", "B", VoteOutcome.TIE))

        assert abs(engine.get_rating("A").rating - before_a) < 1e-9
        assert abs(engine.get_rating("B").rating - before_b) < 1e-9

    def test_upset_win_gives_larger_gain(self):
        """Lower-rated winner gains more than equal-rated winner."""
        engine_equal = EloEngine()
        engine_equal.register_entrant("A")
        engine_equal.register_entrant("B")

        engine_upset = EloEngine(starting_rating=DEFAULT_STARTING_RATING)
        engine_upset.register_entrant("A")
        engine_upset.register_entrant("B")
        # Give B a higher rating so A winning is an upset
        engine_upset.get_rating("B").rating += 400

        ra_equal_before = engine_equal.get_rating("A").rating
        ra_upset_before = engine_upset.get_rating("A").rating

        engine_equal.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        engine_upset.process_match(make_match("A", "B", VoteOutcome.A_WINS))

        gain_equal = engine_equal.get_rating("A").rating - ra_equal_before
        gain_upset = engine_upset.get_rating("A").rating - ra_upset_before

        assert gain_upset > gain_equal

    def test_win_counter_incremented(self):
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        assert engine.get_rating("A").wins == 1
        assert engine.get_rating("B").losses == 1

    def test_loss_counter_incremented(self):
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        engine.process_match(make_match("A", "B", VoteOutcome.B_WINS))
        assert engine.get_rating("A").losses == 1
        assert engine.get_rating("B").wins == 1

    def test_tie_counter_incremented(self):
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        engine.process_match(make_match("A", "B", VoteOutcome.TIE))
        assert engine.get_rating("A").ties == 1
        assert engine.get_rating("B").ties == 1

    def test_matches_played_accumulates(self):
        engine = EloEngine()
        engine.register_entrant("A")
        engine.register_entrant("B")
        engine.register_entrant("C")
        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        engine.process_match(make_match("A", "C", VoteOutcome.B_WINS))  # C wins (B slot)
        assert engine.get_rating("A").matches_played == 2

    def test_auto_register_on_process_match(self):
        """process_match should auto-register unseen entrants."""
        engine = EloEngine()
        engine.process_match(make_match("X", "Y", VoteOutcome.A_WINS))
        assert engine.get_rating("X") is not None
        assert engine.get_rating("Y") is not None

    def test_k_factor_applied(self):
        """With K=16 (half default), rating change should be half of K=32."""
        e32 = EloEngine(k_factor=32.0)
        e16 = EloEngine(k_factor=16.0)
        for e in (e32, e16):
            e.register_entrant("A")
            e.register_entrant("B")

        before_32 = e32.get_rating("A").rating
        before_16 = e16.get_rating("A").rating

        e32.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        e16.process_match(make_match("A", "B", VoteOutcome.A_WINS))

        change_32 = e32.get_rating("A").rating - before_32
        change_16 = e16.get_rating("A").rating - before_16

        assert abs(change_32 - 2 * change_16) < 1e-9

    def test_match_log_grows(self):
        engine = EloEngine()
        assert len(engine.match_log) == 0
        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        assert len(engine.match_log) == 1
        engine.process_match(make_match("B", "C", VoteOutcome.B_WINS))
        assert len(engine.match_log) == 2

    def test_process_matches_batch(self):
        engine = EloEngine()
        matches = [
            make_match("A", "B", VoteOutcome.A_WINS),
            make_match("B", "C", VoteOutcome.B_WINS),
            make_match("A", "C", VoteOutcome.TIE),
        ]
        engine.process_matches(matches)
        assert len(engine.match_log) == 3


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


class TestLeaderboard:
    def test_empty_leaderboard(self):
        engine = EloEngine()
        lb = engine.leaderboard()
        assert lb.ratings == []
        assert lb.total_matches == 0

    def test_leaderboard_sorted_by_rating_desc(self):
        engine = EloEngine()
        # A wins every match: should end up top
        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        engine.process_match(make_match("A", "C", VoteOutcome.A_WINS))
        engine.process_match(make_match("B", "C", VoteOutcome.B_WINS))

        lb = engine.leaderboard()
        ratings = [r.rating for r in lb.ratings]

        assert ratings == sorted(ratings, reverse=True)
        assert lb.ratings[0].entrant_id == "A"

    def test_leaderboard_total_matches(self):
        engine = EloEngine()
        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        engine.process_match(make_match("A", "C", VoteOutcome.A_WINS))
        lb = engine.leaderboard()
        assert lb.total_matches == 2

    def test_leaderboard_caveat_present(self):
        engine = EloEngine()
        lb = engine.leaderboard()
        # The single-voter caveat must always be present
        assert "single-voter" in lb.caveat.lower() or "n=1" in lb.caveat.lower()

    def test_leaderboard_str_contains_entrant_ids(self):
        engine = EloEngine()
        engine.register_entrant("claude-sonnet")
        engine.register_entrant("gpt-4o")
        engine.process_match(make_match("claude-sonnet", "gpt-4o", VoteOutcome.A_WINS))
        lb_str = str(engine.leaderboard())
        assert "claude-sonnet" in lb_str
        assert "gpt-4o" in lb_str

    def test_win_rate_calculation(self):
        engine = EloEngine()
        engine.process_match(make_match("A", "B", VoteOutcome.A_WINS))
        engine.process_match(make_match("A", "C", VoteOutcome.A_WINS))
        engine.process_match(make_match("A", "D", VoteOutcome.B_WINS))

        r = engine.get_rating("A")
        assert r.win_rate == pytest.approx(2 / 3)

    def test_win_rate_none_when_no_matches(self):
        engine = EloEngine()
        engine.register_entrant("A")
        assert engine.get_rating("A").win_rate is None

    def test_leaderboard_ordering_stable_for_model_vs_human_entrants(self):
        """Entrant-generic: human and AI entrants rank together."""
        engine = EloEngine()
        engine.process_match(make_match("human:tony", "gpt-4o", VoteOutcome.A_WINS))
        lb = engine.leaderboard()
        ids = [r.entrant_id for r in lb.ratings]
        assert "human:tony" in ids
        assert "gpt-4o" in ids
        assert ids[0] == "human:tony"  # human won, should be first


# ---------------------------------------------------------------------------
# Sampling strategies
# ---------------------------------------------------------------------------


class TestSamplingStrategies:
    def _populate_engine(self, n_entrants: int) -> EloEngine:
        engine = EloEngine()
        for i in range(n_entrants):
            engine.register_entrant(f"model_{i}")
        return engine

    def test_swiss_returns_adjacent_pairs(self):
        engine = self._populate_engine(4)
        pairs = engine.suggest_next_pairs(SamplingStrategy.SWISS)
        # With 4 entrants [0,1,2,3] we expect (0,1) and (2,3)
        assert len(pairs) == 2
        for a, b in pairs:
            assert a != b

    def test_swiss_with_odd_entrant_count(self):
        engine = self._populate_engine(5)
        pairs = engine.suggest_next_pairs(SamplingStrategy.SWISS)
        # 5 entrants: pairs (0,1), (2,3), and top vs last
        assert len(pairs) == 3

    def test_all_pairs_produces_n_choose_2(self):
        engine = self._populate_engine(4)
        pairs = engine.suggest_next_pairs(SamplingStrategy.ALL_PAIRS)
        assert len(pairs) == 6  # C(4,2) = 6

    def test_random_strategy_returns_pairs(self):
        engine = self._populate_engine(6)
        pairs = engine.suggest_next_pairs(SamplingStrategy.RANDOM)
        assert len(pairs) >= 1
        for a, b in pairs:
            assert a != b

    def test_n_pairs_cap_respected(self):
        engine = self._populate_engine(6)
        pairs = engine.suggest_next_pairs(SamplingStrategy.ALL_PAIRS, n_pairs=3)
        assert len(pairs) == 3

    def test_no_self_pairs(self):
        engine = self._populate_engine(6)
        for strategy in SamplingStrategy:
            pairs = engine.suggest_next_pairs(strategy)
            for a, b in pairs:
                assert a != b, f"Self-pair found with {strategy}: ({a}, {b})"

    def test_empty_engine_returns_no_pairs(self):
        engine = EloEngine()
        assert engine.suggest_next_pairs() == []

    def test_single_entrant_returns_no_pairs(self):
        engine = EloEngine()
        engine.register_entrant("solo")
        assert engine.suggest_next_pairs() == []

    def test_swiss_avoids_all_pairs_blowup(self):
        """Swiss should produce O(M) pairs, not O(M²)."""
        engine = self._populate_engine(20)
        swiss_pairs = engine.suggest_next_pairs(SamplingStrategy.SWISS)
        all_pairs = engine.suggest_next_pairs(SamplingStrategy.ALL_PAIRS)
        # Swiss should be significantly fewer
        assert len(swiss_pairs) <= len(all_pairs) / 2
