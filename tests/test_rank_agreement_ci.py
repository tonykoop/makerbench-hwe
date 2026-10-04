"""Rank-agreement headline + bootstrap CI (#857)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench import rank_agreement_ci as rac

REPO = Path(__file__).resolve().parents[1]
ARENA = REPO / "site" / "data" / "arena.json"
SUMMARY = REPO / "docs" / "showcase" / "data" / "agreement-rounds-6-10.json"
BANNED_KEY_TOKENS = ("elo", "vote", "rating", "subjective", "winner", "ballot", "judge")


def _fixture_arena() -> dict:
    rounds = [
        {"round": 5, "agreement": {"rho": -1.0, "n": 2}},  # degenerate, excluded
        {"round": 6, "agreement": {"rho": -0.5, "n": 3}},
        {"round": 7, "agreement": {"rho": -0.5, "n": 3}},
        {"round": 8, "agreement": {"rho": 0.866, "n": 3}},
        {"round": 9, "agreement": {"rho": 0.5, "n": 3}},
        {"round": 10, "agreement": {"rho": 0.0, "n": 3}},
        {"round": 11, "agreement": {"rho": 1.0, "n": 3}},  # outside window
    ]
    return {"rounds": rounds}


def test_reproduces_mean_from_fixture():
    summary = rac.build_summary(_fixture_arena(), n_boot=2000)
    assert summary["rounds_used"] == [6, 7, 8, 9, 10]
    assert summary["mean_rank_agreement_rho"] == pytest.approx(0.0732, abs=1e-4)


def test_reproduces_committed_headline():
    arena = json.loads(ARENA.read_text(encoding="utf-8"))
    summary = rac.build_summary(arena)
    assert summary["mean_rank_agreement_rho"] == pytest.approx(arena["headline"]["value"], abs=1e-4)
    assert summary["rounds_used"] == arena["headline"]["rounds_used"]


def test_committed_summary_is_current():
    arena = json.loads(ARENA.read_text(encoding="utf-8"))
    expected = json.dumps(rac.build_summary(arena), indent=2, ensure_ascii=False) + "\n"
    assert SUMMARY.read_text(encoding="utf-8") == expected


def test_ci_contains_point_estimate_and_matches_exact():
    s = rac.build_summary(_fixture_arena())
    assert s["ci95_low"] <= s["mean_rank_agreement_rho"] <= s["ci95_high"]
    assert s["ci95_low"] == pytest.approx(s["ci95_exact_low"], abs=0.03)
    assert s["ci95_high"] == pytest.approx(s["ci95_exact_high"], abs=0.03)


def test_deterministic_with_seed():
    rhos = [-0.5, -0.5, 0.866, 0.5, 0.0]
    assert rac.round_bootstrap_ci(rhos, seed=1) == rac.round_bootstrap_ci(rhos, seed=1)
    assert rac.round_bootstrap_ci(rhos, seed=1, n_boot=500) != rac.round_bootstrap_ci(
        rhos, seed=2, n_boot=500
    )
    pairs = [[(1, 2), (2, 1), (3, 3)], [(1, 1), (2, 2), (3, 3), (4, 5)]]
    assert rac.entrant_bootstrap_ci(pairs, seed=3, n_boot=300) == rac.entrant_bootstrap_ci(
        pairs, seed=3, n_boot=300
    )


def test_spearman_ties_and_degenerate():
    assert rac.spearman_rho([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert rac.spearman_rho([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    # Tie on one side: average ranks, matches the published R8 value.
    assert rac.spearman_rho([1, 2, 3], [1, 2, 2]) == pytest.approx(0.866, abs=1e-3)
    assert rac.spearman_rho([1, 1, 1], [1, 2, 3]) is None  # no variance
    assert rac.spearman_rho([1], [1]) is None  # n < 2
    with pytest.raises(ValueError):
        rac.spearman_rho([1, 2], [1])


def test_rounds_below_min_entrants_excluded():
    arena = {"rounds": [{"round": 6, "agreement": {"rho": 1.0, "n": 2}}]}
    assert rac.load_round_rhos(arena) == []
    with pytest.raises(ValueError):
        rac.build_summary(arena)


def test_single_round_ci_collapses():
    assert rac.round_bootstrap_ci([0.25], n_boot=50) == (0.25, 0.25)
    assert rac.exact_round_bootstrap_ci([0.25]) == (0.25, 0.25)


def test_entrant_bootstrap_reports_degeneracy():
    out = rac.entrant_bootstrap_ci([[(1, 1), (2, 2), (3, 3)]], n_boot=2000)
    # Three entrants: 3/27 resamples are one entrant repeated (rho undefined).
    assert out["undefined_round_share"] == pytest.approx(3 / 27, abs=0.03)
    assert out["ci95_low"] <= 1.0 <= out["ci95_high"] + 1e-9


def test_summary_has_only_aggregate_keys():
    s = rac.build_summary(_fixture_arena(), n_boot=100)

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    for key in keys(s):
        assert not any(tok in key.lower() for tok in BANNED_KEY_TOKENS), key


def test_cli_check_mode(tmp_path):
    out = tmp_path / "s.json"
    assert rac.main(["--arena", str(ARENA), "--out", str(out), "--n-boot", "200"]) == 0
    assert rac.main(["--arena", str(ARENA), "--out", str(out), "--n-boot", "200", "--check"]) == 0
    out.write_text("{}", encoding="utf-8")
    assert rac.main(["--arena", str(ARENA), "--out", str(out), "--n-boot", "200", "--check"]) == 1
