"""Sparse perfect samples must not lead the overall ranked board (#836)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("site_coverage", ROOT / "site/build_data.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_sparse_sample_cannot_take_hero_or_static_rank_one():
    models = [
        {"identifier": "sparse-perfect", "tracks": {"blind": {"overall_mean": 4.0,
          "n_families_scored": 1}}},
        {"identifier": "broad-result", "tracks": {"blind": {"overall_mean": 3.5,
          "n_families_scored": 4}}},
    ]
    hero = builder.build_hero_stats(models, [{"id": str(i)} for i in range(49)])
    top = next(s for s in hero["stats"] if s["key"] == "top_score")
    assert top["value"] == 3.5
    assert "broad-result" in top["detail"]
    table = builder._prerender_leaderboard_html({"models": models})
    assert "broad-result" in table
    assert "sparse-perfect" not in table
    card = builder.leaderboard_og_svg({"models": models})
    assert "Leader: broad-result" in card
    assert "sparse-perfect" not in card


def test_no_overall_leader_is_fabricated_from_only_sparse_samples():
    models = [{"identifier": "sample", "tracks": {"blind": {
        "overall_mean": 4.0, "n_families_scored": 1}}}]
    assert not any(s["key"] == "top_score" for s in builder.build_hero_stats(models, [])['stats'])
    assert "Leader:" not in builder.leaderboard_og_svg({"models": models})
