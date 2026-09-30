"""Read-only CLI projections, with unknown and invalid telemetry preserved."""

import json

import pytest
from typer.testing import CliRunner

from makerbench.cli import app
from makerbench.arena_studio import doe
from telemetry.schema import SessionTelemetry
from telemetry.store import append


def test_cli_projects_actual_telemetry_without_dispatch(tmp_path, monkeypatch):
    store = tmp_path / "sessions.jsonl"
    for n, (cost, duration) in enumerate(((2.0, 10), (4.0, 20))):
        append(SessionTelemetry(session_id=str(n), agent_id="openrouter-deepseek-v4-pro",
                                duration_seconds=duration, telemetry={"cost_usd": cost}), str(store))
    def refuse(*args, **kwargs):
        raise AssertionError("An estimate must not launch a run")
    monkeypatch.setattr("makerbench.cli_arena.run_orchestration", refuse)
    result = CliRunner().invoke(app, ["arena", "estimate", "--models", "openrouter-deepseek-v4-pro",
                                     "--trials", "6", "--telemetry-store", str(store), "--budget-usd", "20"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["models"][0]["n_cost_samples"] == 2
    assert data["models"][0]["projected_cost_usd"] == 18
    assert data["models"][0]["projected_duration_s"] == 90
    assert data["projected_total_usd"] == 18
    assert data["projected_serial_duration_s"] == 90
    assert data["within_budget"] is True
    assert data["execution"] == "estimate_only"


def test_unknown_model_makes_total_unknown_instead_of_free(tmp_path):
    store = tmp_path / "sessions.jsonl"
    append(SessionTelemetry(session_id="1", agent_id="openrouter-deepseek-v4-pro",
                            duration_seconds=10, telemetry={"cost_usd": 2}), str(store))
    result = CliRunner().invoke(app, ["arena", "estimate", "--models",
        "openrouter-deepseek-v4-pro,openrouter-glm-5.2", "--telemetry-store", str(store)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["projected_total_usd"] is None
    assert data["known_projected_cost_usd"] == 2
    assert data["projected_serial_duration_s"] is None
    assert data["unknown_cost_models"] == ["openrouter-glm-5.2"]


@pytest.mark.parametrize("cost", [-1, float("nan"), float("inf"), True])
def test_invalid_cost_history_does_not_become_a_projection(tmp_path, cost):
    store = tmp_path / "sessions.jsonl"
    append(SessionTelemetry(session_id="1", agent_id="openrouter-deepseek-v4-pro",
                            duration_seconds=10, telemetry={"cost_usd": cost}), str(store))
    result = doe.estimate_model_cost_and_time("openrouter-deepseek-v4-pro", telemetry_store=str(store))
    assert result["cost_usd"] is None
    assert result["n_cost_samples"] == 0


def test_subscription_cost_is_zero_but_missing_duration_is_unknown(tmp_path):
    result = CliRunner().invoke(app, ["arena", "estimate", "--models", "codex-gpt-6.1-sol",
                                     "--telemetry-store", str(tmp_path / "absent")])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["projected_total_usd"] == 0
    assert data["projected_serial_duration_s"] is None


def test_nonpositive_trials_and_malformed_history_are_rejected(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["arena", "estimate", "--models", "x", "--trials", "0"]).exit_code != 0
    store = tmp_path / "bad.jsonl"
    store.write_text("not-json\n")
    result = runner.invoke(app, ["arena", "estimate", "--models", "openrouter-glm-5.2",
                                 "--telemetry-store", str(store)])
    assert result.exit_code != 0
