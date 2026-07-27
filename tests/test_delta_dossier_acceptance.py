"""Issue #108 acceptance lock for the Delta-Dossier regression tracker."""

from __future__ import annotations

import json
from pathlib import Path

from makerbench.delta_dossier import build_delta_dossier

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "DELTA_DOSSIER.md"


def _run(
    revision: int,
    *,
    seed: int = 17,
    task_id: str = "vented_plate",
    score: int,
    wall_time: float,
    tool_calls: int,
    hii: str,
    host: str = "OpenSCAD",
) -> dict:
    return {
        "benchmark_version": "0.1.0",
        "result_provenance": "community",
        "model_identifier": "codex-gpt-5.5",
        "agent_identifier": "codex_cli",
        "harness_class": "assisted-workflow",
        "harness_subclass": "api-driven-code",
        "results": [
            {
                "task_id": task_id,
                "seed": seed,
                "track": "blind",
                "certificate_history": [
                    {
                        "revision": revision,
                        "issued_at": f"2026-06-{revision:02d}T00:00:00Z",
                    }
                ],
                "workflow_manifest": {
                    "stack": {
                        "orchestrator": "Codex GPT-5.5",
                        "framework": "makerbench-logger",
                        "host_application": host,
                    },
                    "human_intervention_index": {"overall": hii},
                    "metrics": {
                        "wall_clock_seconds": wall_time,
                        "tool_calls_count": tool_calls,
                    },
                },
                "grade": {"score": score, "levels": []},
                "dossier_scores": {"score": score / 4, "max_score": 1.0},
            }
        ],
    }


def test_story_108_tracks_four_deltas_for_same_stack_seed_over_revisions(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "rev1.json").write_text(
        json.dumps(_run(1, score=3, wall_time=120, tool_calls=10, hii="L2")),
        encoding="utf-8",
    )
    (results_dir / "rev2.json").write_text(
        json.dumps(_run(2, score=4, wall_time=90, tool_calls=6, hii="L1")),
        encoding="utf-8",
    )

    payload = build_delta_dossier(results_dir)

    assert payload["score_impact"] == "none"
    assert payload["summary"]["n_comparable_series"] == 1
    series = payload["stacks"][0]["series"][0]
    delta = series["delta"]

    assert series["baseline"]["revision_id"] == "1"
    assert series["latest"]["revision_id"] == "2"
    assert "seed" not in series
    assert series["seed_ordinal"] == 1
    assert delta["score"] == 1.0
    assert delta["score_trend"] == "improved"
    assert delta["wall_time_s"] == -30.0
    assert delta["wall_time_reduction_pct"] == 25.0
    assert delta["wall_time_trend"] == "improved"
    assert delta["tool_calls"] == -4.0
    assert delta["tool_call_reduction_pct"] == 40.0
    assert delta["tool_call_trend"] == "improved"
    assert delta["hii_rank"] == -1.0
    assert delta["hii_trend"] == "down"


def test_story_108_does_not_compare_different_seeds_or_stacks(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "rev1.json").write_text(
        json.dumps(_run(1, score=3, wall_time=120, tool_calls=10, hii="L2")),
        encoding="utf-8",
    )
    (results_dir / "different_seed.json").write_text(
        json.dumps(_run(2, seed=18, score=4, wall_time=90, tool_calls=6, hii="L1")),
        encoding="utf-8",
    )
    (results_dir / "different_stack.json").write_text(
        json.dumps(_run(3, score=4, wall_time=88, tool_calls=5, hii="L1", host="Blender")),
        encoding="utf-8",
    )

    default_payload = build_delta_dossier(results_dir)
    diagnostic = build_delta_dossier(results_dir, include_singletons=True)

    assert default_payload["summary"]["n_comparable_series"] == 0
    assert default_payload["stacks"] == []
    assert diagnostic["summary"]["n_series_total"] == 3
    assert all(series["n_revisions"] == 1 for stack in diagnostic["stacks"] for series in stack["series"])


def test_story_108_docs_name_ci_cd_and_certificate_history_contract():
    text = DOC.read_text(encoding="utf-8")

    for needle in (
        "CI/CD tool",
        "wall-clock time",
        "tool calls",
        "Human Intervention Index",
        "geometry score",
        "certificate_history",
        "score_impact",
        '"none"',
        "seed_ordinal",
    ):
        assert needle in text
