"""Tests for the Code-CAD Arena Elo engine (#425)."""

from pathlib import Path

import pytest

from makerbench import code_cad_arena as arena


def test_pairwise_vote_updates_ratings_and_counts():
    report = arena.build_elo_leaderboard(
        [
            arena.Vote(
                left="gpt-5.5",
                right="sonnet",
                winner="left",
                instrument_id="lyre",
                voter_id="tony",
            ),
            arena.Vote(
                left="sonnet",
                right="gemini",
                winner="right",
                instrument_id="lyre",
                voter_id="tony",
            ),
            arena.Vote(
                left="gpt-5.5",
                right="gemini",
                winner="draw",
                instrument_id="kora",
                voter_id="tony",
            ),
        ]
    )

    rows = {row["entrant"]: row for row in report["leaderboard"]}
    assert report["schema"] == arena.SCHEMA
    assert report["votes"] == 3
    assert report["voters"] == 1
    assert report["instruments"] == 2
    assert rows["gpt-5.5"]["games"] == 2
    assert rows["gpt-5.5"]["wins"] == 1
    assert rows["gpt-5.5"]["draws"] == 1
    assert rows["sonnet"]["losses"] == 2
    assert rows["gpt-5.5"]["rating"] > rows["gemini"]["rating"] > rows["sonnet"]["rating"]


def test_leaderboard_includes_unplayed_entrants_at_initial_rating():
    report = arena.build_elo_leaderboard(
        [{"left": "a", "right": "b", "winner": "left"}],
        entrants=["a", "b", "c"],
    )
    rows = {row["entrant"]: row for row in report["leaderboard"]}
    assert rows["c"] == {
        "rank": 2,
        "entrant": "c",
        "rating": 1500.0,
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
    }


def test_draw_between_equal_ratings_keeps_ratings_equal():
    new_a, new_b = arena.update_ratings(1500.0, 1500.0, 0.5)
    assert new_a == pytest.approx(1500.0)
    assert new_b == pytest.approx(1500.0)


def test_invalid_vote_rejected():
    with pytest.raises(ValueError, match="winner"):
        arena.build_elo_leaderboard([{"left": "a", "right": "b", "winner": "both"}])

    with pytest.raises(ValueError, match="must differ"):
        arena.build_elo_leaderboard([arena.Vote(left="a", right="a", winner="draw")])


def test_swiss_pairs_are_deterministic_and_linear_size():
    entrants = ["gpt-5.5", "sonnet", "gemini", "codex-spark", "human"]
    ratings = {"gpt-5.5": 1540, "sonnet": 1520, "gemini": 1480, "codex-spark": 1460, "human": 1500}

    pairs_a = arena.sample_swiss_pairs(entrants, ratings=ratings, round_index=3, seed="lyre")
    pairs_b = arena.sample_swiss_pairs(
        list(reversed(entrants)),
        ratings=ratings,
        round_index=3,
        seed="lyre",
    )

    assert pairs_a == pairs_b
    assert len(pairs_a) == 2
    assert len({entrant for pair in pairs_a for entrant in pair}) == 4


def test_swiss_pairs_support_round_rotation_and_pair_cap():
    entrants = ["a", "b", "c", "d", "e", "f"]
    first = arena.sample_swiss_pairs(entrants, round_index=0, seed="arena")
    second = arena.sample_swiss_pairs(entrants, round_index=1, seed="arena")
    capped = arena.sample_swiss_pairs(entrants, round_index=0, seed="arena", max_pairs=2)

    assert first != second
    assert len(first) == 3
    assert len(capped) == 2


def test_doc_records_sampling_and_single_voter_caveat():
    doc = Path(__file__).resolve().parents[1] / "docs" / "CODE_CAD_ARENA.md"
    text = doc.read_text(encoding="utf-8")
    assert "Swiss-style" in text
    assert "adjacent-rating sampling" in text
    assert "Single-voter" in text
    assert "Refs #421" not in text
