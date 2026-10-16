"""Sparse perfect samples must not lead the overall ranked board (#836)."""

import importlib.util
from pathlib import Path

import pytest

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
    assert top["label"] == (
        f"top blind score among models measured on ≥{builder.OVERALL_RANKING_MIN_FAMILIES} families"
    )
    assert "broad-result" in top["detail"]
    table = builder._prerender_leaderboard_html({"models": models})
    assert "broad-result" in table
    assert "Limited coverage · unranked" in table
    ranked, partial = table.split("Limited coverage · unranked", 1)
    assert "sparse-perfect" not in ranked
    assert "sparse-perfect" in partial
    card = builder.leaderboard_og_svg({"models": models})
    assert "Leader: broad-result" in card
    assert "sparse-perfect" not in card


def test_no_overall_leader_is_fabricated_from_only_sparse_samples():
    models = [{"identifier": "sample", "tracks": {"blind": {
        "overall_mean": 4.0, "n_families_scored": 1}}}]
    top = next(s for s in builder.build_hero_stats(models, [])['stats'] if s['key'] == 'top_score')
    assert top['value'] is None
    assert top['display'] == '—'
    assert top['detail'] == 'No model meets the coverage minimum'
    assert "Leader:" not in builder.leaderboard_og_svg({"models": models})


@pytest.mark.parametrize("models", [[], [
    {"identifier": "unknown-coverage", "tracks": {"blind": {"overall_mean": 4.0}}},
]])
def test_hero_explicitly_reports_no_qualifying_model(models):
    top = builder.build_hero_stats(models, [])['stats'][0]
    assert top['key'] == 'top_score'
    assert top['value'] is None
    assert top['detail'] == 'No model meets the coverage minimum'


def test_hero_selects_highest_qualifying_score_independent_of_input_order():
    minimum = builder.OVERALL_RANKING_MIN_FAMILIES
    models = [
        {"identifier": name, "tracks": {"blind": {
            "overall_mean": score, "n_families_scored": count}}}
        for name, score, count in [
            ("lower", 2.0, minimum), ("winner", 3.5, minimum),
            ("narrow", 4.0, minimum - 1),
        ]
    ]
    top = builder.build_hero_stats(models, [{"id": str(i)} for i in range(49)])['stats'][0]
    assert top['value'] == 3.5
    assert top['detail'] == f'winner · {minimum}/49 families'
