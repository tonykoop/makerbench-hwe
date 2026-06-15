"""Contract tests for the `makerbench delta-dossier` CLI (#108).

The Delta-Dossier engine and its site payload land elsewhere; this exercises the
runnable entry point so a developer can get a regression report on a results
directory without writing Python — the "CI for hardware-AI" use case.
"""

import json

from typer.testing import CliRunner

from makerbench.cli import app

runner = CliRunner()


def _workflow_run(revision, *, score, wall_time, tool_calls, hii):
    return {
        "benchmark_version": "0.1.0",
        "result_provenance": "community",
        "model_identifier": "codex-gpt-5.5",
        "reasoning_level": "high",
        "agent_identifier": "codex_cli",
        "harness_class": "assisted-workflow",
        "results": [
            {
                "task_id": "vented_plate",
                "seed": 17,
                "track": "blind",
                "certificate_history": [
                    {"revision": revision, "issued_at": f"2026-06-0{revision}T00:00:00Z"}
                ],
                "workflow_manifest": {
                    "run_id": f"codex-v{revision}",
                    "stack": {"orchestrator": "Codex GPT-5.5", "host_application": "OpenSCAD"},
                    "human_intervention_index": {"overall": hii},
                    "metrics": {"wall_clock_seconds": wall_time, "tool_calls_count": tool_calls},
                },
                "grade": {"task_id": "vented_plate", "track": "blind", "score": score, "levels": []},
            }
        ],
    }


def _seed_results(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "rev1.json").write_text(
        json.dumps(_workflow_run(1, score=3, wall_time=120, tool_calls=10, hii="L2")),
        encoding="utf-8",
    )
    (results_dir / "rev2.json").write_text(
        json.dumps(_workflow_run(2, score=4, wall_time=90, tool_calls=6, hii="L1")),
        encoding="utf-8",
    )
    return results_dir


def test_delta_dossier_cli_reports_comparable_series(tmp_path):
    results_dir = _seed_results(tmp_path)
    result = runner.invoke(app, ["delta-dossier", str(results_dir)])

    assert result.exit_code == 0
    # The summary line and the stack table both render.
    assert "1 comparable" in result.stdout
    assert "vented_plate" in result.stdout
    assert "improved" in result.stdout
    # Disclosure guarantee surfaced; never a score input.
    assert "score_impact" in result.stdout


def test_delta_dossier_cli_json_is_structured(tmp_path):
    results_dir = _seed_results(tmp_path)
    result = runner.invoke(app, ["delta-dossier", "--json", str(results_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["score_impact"] == "none"
    assert payload["summary"]["n_comparable_series"] == 1
    series = payload["stacks"][0]["series"][0]
    assert series["task_id"] == "vented_plate"
    assert series["delta"]["score_trend"] == "improved"
    # Seed ordinals only — no raw seed leak.
    assert "seed" not in series
    assert series["seed_ordinal"] == 1


def test_delta_dossier_cli_empty_dir_is_clean(tmp_path):
    empty = tmp_path / "results"
    empty.mkdir()
    result = runner.invoke(app, ["delta-dossier", str(empty)])

    assert result.exit_code == 0
    assert "No comparable reruns" in result.stdout


def test_delta_dossier_cli_include_singletons_lists_single_runs(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "rev1.json").write_text(
        json.dumps(_workflow_run(1, score=3, wall_time=120, tool_calls=10, hii="L1")),
        encoding="utf-8",
    )

    plain = runner.invoke(app, ["delta-dossier", "--json", str(results_dir)])
    diagnostic = runner.invoke(
        app, ["delta-dossier", "--include-singletons", "--json", str(results_dir)]
    )

    assert json.loads(plain.stdout)["stacks"] == []
    assert json.loads(diagnostic.stdout)["stacks"][0]["series"][0]["n_revisions"] == 1
