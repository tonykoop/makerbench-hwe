"""Cost capture and the --max-cost guard for metered OpenRouter entrants. HTTP is stubbed: no live calls."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from makerbench import code_cad_providers as providers
from makerbench.cli import app
from makerbench.code_cad_generator import run_generation_batch
from makerbench.code_cad_orchestrator import OrchestrationConfig, run_orchestration
from makerbench.code_cad_providers import BudgetExhausted, MeteredBudget, MeteredCostError

FENCED = "```scad\ncube(1);\n```"
REGISTRY = {"instruments": [{"id": "udu", "display_name": "Udu", "task_kind": "single_part_vessel", "envelope_mm": [1, 1, 1]}]}


def _ok(cost, tokens=(100, 50)):
    usage = {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}
    if cost is not None:
        usage["cost"] = cost
    return {"choices": [{"message": {"content": FENCED}}], "usage": usage}


@pytest.fixture
def http(monkeypatch):
    calls = []
    queue: list = []

    def fake_request(path, payload, *, timeout_s):
        calls.append({"path": path, "payload": payload})
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(providers, "_openrouter_request", fake_request)
    monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
    return calls, queue


def _request(seed=0):
    return providers.GenerationRequest(
        model_id="openrouter-x", instrument_id="udu", seed=seed, spec={}, prompt="p", prompt_sha256="0" * 64
    )


def test_actual_cost_and_tokens_land_in_trial_provenance(http, tmp_path):
    calls, queue = http
    queue.append(_ok(0.0123))
    gen = providers.make_openrouter_generator("acme/model-x")
    results = run_generation_batch(
        registry=REGISTRY, instrument_id="udu", seed=0, model_ids=["openrouter-x"], generator=gen, out_dir=tmp_path
    )
    assert results[0].status == "ok"
    assert calls[0]["payload"]["usage"] == {"include": True}
    usage = json.loads(results[0].provenance_path.read_text(encoding="utf-8"))["usage"]
    assert usage == {"slug": "acme/model-x", "cost_usd": 0.0123, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_cli_entrants_have_no_usage_block(tmp_path):
    gen = providers.make_stub_generator()
    results = run_generation_batch(
        registry=REGISTRY, instrument_id="udu", seed=0, model_ids=["stub-a"], generator=gen, out_dir=tmp_path
    )
    assert "usage" not in json.loads(results[0].provenance_path.read_text(encoding="utf-8"))


def test_failed_request_makes_one_call_and_halts_a_budgeted_run(http, tmp_path):
    calls, queue = http
    queue.append(RuntimeError("502"))
    budget = MeteredBudget(5.0, tmp_path / "ledger.jsonl")
    gen = providers.make_openrouter_generator("acme/model-x", budget=budget)
    with pytest.raises(MeteredCostError, match="cost unknown"):
        gen(_request())
    assert len(calls) == 1
    assert budget.halt_reason and budget.halt_call_made


def test_missing_cost_halts_and_is_recorded_as_unknown(http, tmp_path):
    calls, queue = http
    queue.append(_ok(None))
    budget = MeteredBudget(5.0, tmp_path / "ledger.jsonl")
    gen = providers.make_openrouter_generator("acme/model-x", budget=budget)
    with pytest.raises(MeteredCostError, match="usable usage.cost"):
        gen(_request())
    row = json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["cost_usd"] is None
    with pytest.raises(BudgetExhausted):
        gen(_request(seed=1))
    assert len(calls) == 1


@pytest.mark.parametrize("bad", [-0.01, float("nan"), "0.02", True])
def test_unreadable_cost_values_are_unknown(bad):
    assert providers._usage_cost({"usage": {"cost": bad}})[0] is None


def test_cap_stops_before_a_call_that_could_cross_it(http, tmp_path):
    calls, queue = http
    queue.append(_ok(0.04))
    budget = MeteredBudget(0.10, tmp_path / "ledger.jsonl")
    gen = providers.make_openrouter_generator("acme/model-x", budget=budget)
    gen(_request(0))
    gen(_request(1))
    assert budget.cumulative_usd == pytest.approx(0.08)
    with pytest.raises(BudgetExhausted, match="cap"):
        gen(_request(2))
    assert len(calls) == 2
    assert budget.halt_reason and not budget.halt_call_made


def test_ledger_carries_the_cumulative_total_across_batches(http, tmp_path):
    _, queue = http
    queue.append(_ok(0.03))
    ledger = tmp_path / "ledger.jsonl"
    gen = providers.make_openrouter_generator("acme/model-x", budget=MeteredBudget(1.0, ledger))
    gen(_request(0))
    resumed = MeteredBudget(1.0, ledger)
    assert resumed.cumulative_usd == pytest.approx(0.03) and resumed.n_calls == 1
    assert json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])["cumulative_usd"] == pytest.approx(0.03)


def test_max_cost_must_be_positive_and_finite():
    for bad in (0, -1, float("inf")):
        with pytest.raises(ValueError):
            MeteredBudget(bad)


def _config(n_seeds):
    return OrchestrationConfig(
        instrument_ids=("udu",), model_ids=("openrouter-x",), seeds=tuple(range(n_seeds)), max_attempts=2,
        model_providers={"openrouter-x": "openrouter"},
    )


def test_orchestrator_halts_leaves_unsent_trials_pending_and_gives_the_attempt_back(http, tmp_path):
    _, queue = http
    queue.append(_ok(0.04))
    budget = MeteredBudget(0.10)
    gen = providers.make_openrouter_generator("acme/model-x", budget=budget)

    def execute(trial):
        gen(_request(trial.seed))
        return {"status": "scored"}

    run_log = tmp_path / "run_log.json"
    log = run_orchestration(config=_config(4), run_log_path=run_log, execute_trial=execute, budget=budget)
    rows = {t["seed"]: t for t in log["trials"]}
    assert [rows[s]["status"] for s in (0, 1)] == ["scored", "scored"]
    assert rows[2]["status"] == "pending" and rows[2]["attempts"] == 0 and "not run" in rows[2]["error"]
    assert rows[3]["status"] == "pending" and rows[3]["attempts"] == 0
    assert budget.halt_reason and not budget.halt_call_made


def test_orchestrator_keeps_the_attempt_when_the_halting_call_was_sent(http, tmp_path):
    _, queue = http
    queue.append(_ok(None))
    budget = MeteredBudget(5.0)
    gen = providers.make_openrouter_generator("acme/model-x", budget=budget)

    def execute(trial):
        gen(_request(trial.seed))
        return {"status": "scored"}

    log = run_orchestration(config=_config(3), run_log_path=tmp_path / "run_log.json", execute_trial=execute, budget=budget)
    rows = {t["seed"]: t for t in log["trials"]}
    assert rows[0]["status"] == "error" and rows[0]["attempts"] == 1
    assert rows[1]["status"] == "pending" and rows[2]["status"] == "pending"


def test_cli_refuses_an_openrouter_entrant_without_max_cost(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    result = CliRunner().invoke(app, ["arena", "run", "--run-dir", str(tmp_path / "r"), "--instruments", "udu", "--models", "openrouter-glm-5.2"])
    assert result.exit_code == 1
    assert "--max-cost" in result.output


def test_cli_stub_run_with_an_openrouter_id_needs_no_cap(monkeypatch, tmp_path):
    result = CliRunner().invoke(
        app, ["arena", "run", "--run-dir", str(tmp_path / "r"), "--instruments", "ocarina", "--models", "openrouter-glm-5.2", "--stub"]
    )
    assert "--max-cost" not in result.output
