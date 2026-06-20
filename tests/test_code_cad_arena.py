"""Tests for the Code-CAD A/B arena Elo slice (#425)."""

import pytest

from makerbench import code_cad_arena as arena


def test_single_vote_updates_equal_ratings_symmetrically():
    vote = arena.PairwiseVote("lyre-s0-a", "gpt-5.5", "sonnet", "left")

    rows = arena.build_elo_leaderboard([vote])

    assert [row["model"] for row in rows] == ["gpt-5.5", "sonnet"]
    assert rows[0]["rating"] == 1016.0
    assert rows[0]["wins"] == 1
    assert rows[1]["rating"] == 984.0
    assert rows[1]["losses"] == 1
    assert rows[0]["last_delta"] == 16.0
    assert rows[1]["last_delta"] == -16.0


def test_tie_records_draw_without_moving_equal_ratings():
    vote = arena.PairwiseVote("kora-s1-a", "codex-spark", "gemini", "tie")

    rows = arena.build_elo_leaderboard([vote])

    assert {row["rating"] for row in rows} == {1000.0}
    assert {row["ties"] for row in rows} == {1}
    assert {row["votes"] for row in rows} == {1}


def test_vote_sequence_builds_sorted_leaderboard():
    votes = [
        arena.PairwiseVote("fujara-s0-a", "gpt-5.5", "sonnet", "left"),
        arena.PairwiseVote("fujara-s0-b", "gpt-5.5", "gemini", "left"),
        arena.PairwiseVote("duduk-s2-a", "sonnet", "gemini", "right"),
    ]

    rows = arena.build_elo_leaderboard(votes)

    assert [row["model"] for row in rows] == ["gpt-5.5", "gemini", "sonnet"]
    assert rows[0]["votes"] == 2
    assert rows[0]["wins"] == 2
    assert rows[2]["losses"] == 2
    assert rows[0]["rating"] > rows[1]["rating"] > rows[2]["rating"]


def test_apply_vote_does_not_mutate_input_mapping():
    original = {"gpt-5.5": arena.RatingState("gpt-5.5", rating=1200.0)}
    vote = arena.PairwiseVote("lyre-s0-a", "gpt-5.5", "sonnet", "right")

    updated = arena.apply_vote(original, vote)

    assert original["gpt-5.5"].rating == 1200.0
    assert updated["gpt-5.5"].rating < 1200.0
    assert updated["sonnet"].rating > 1000.0


def test_invalid_vote_inputs_are_rejected():
    with pytest.raises(ValueError, match="must differ"):
        arena.PairwiseVote("trial", "gpt-5.5", "gpt-5.5", "left")
    with pytest.raises(ValueError, match="winner"):
        arena.PairwiseVote("trial", "gpt-5.5", "sonnet", "center")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="k_factor"):
        arena.apply_vote({}, arena.PairwiseVote("trial", "gpt-5.5", "sonnet", "left"), k_factor=0)


def test_bounded_pair_sample_is_deterministic_and_not_all_pairs():
    models = ["gpt-5.5", "sonnet", "codex-spark", "gemini", "qwen", "kimi", "grok", "deepseek"]

    pairs = arena.bounded_pair_sample(models, seed=421, max_pairs_per_model=2)
    again = arena.bounded_pair_sample(list(reversed(models)), seed=421, max_pairs_per_model=2)

    assert pairs == again
    assert len(pairs) <= len(models)
    assert len(pairs) < 28  # 8 choose 2 all-pairs blowup
    for model in models:
        assert sum(model in pair for pair in pairs) <= 2
    assert all(a != b for a, b in pairs)


def test_bounded_pair_sample_prefers_pairs_with_less_history():
    models = ["gpt-5.5", "sonnet", "gemini", "qwen"]

    pairs = arena.bounded_pair_sample(
        models,
        seed=1,
        max_pairs_per_model=1,
        prior_pair_counts={("gemini", "qwen"): 99, ("gpt-5.5", "sonnet"): 99},
    )

    assert ("gemini", "qwen") not in pairs
    assert ("gpt-5.5", "sonnet") not in pairs
    assert len(pairs) == 2


def test_markdown_leaderboard_view_contains_per_model_rows():
    rows = arena.build_elo_leaderboard(
        [
            arena.PairwiseVote("trial-a", "gpt-5.5", "sonnet", "left"),
            arena.PairwiseVote("trial-b", "gemini", "sonnet", "tie"),
        ]
    )

    table = arena.render_markdown_leaderboard(rows)

    assert "| Rank | Model | Elo | Votes | W-L-T | Last delta |" in table
    assert "`gpt-5.5`" in table
    assert "`sonnet`" in table
    assert "`gemini`" in table
    assert "1-0-0" in table
