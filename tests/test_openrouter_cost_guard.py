"""Cost capture and the pre-dispatch --max-cost cap for metered OpenRouter entrants. HTTP is stubbed: no live calls."""

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
# max billable cost of one request with these prices at the default 16,000-token cap: ~$0.5
EXPENSIVE = {"prompt": "0.0", "completion": "0.00003125"}
CHEAP = {"prompt": "0.000001", "completion": "0.000002"}


def _ok(cost, tokens=(100, 50), rid="gen-1"):
    usage = {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}
    if cost is not None:
        usage["cost"] = cost
    return {"id": rid, "choices": [{"message": {"content": FENCED}}], "usage": usage}


@pytest.fixture
def http(monkeypatch):
    """Stub the OpenRouter client: /models serves ``state['pricing']``; chat replies come from ``queue``."""

    calls: list = []
    queue: list = []
    state = {"pricing": CHEAP}

    def fake_request(path, payload, *, timeout_s):
        if path == "/models":
            entry = {"id": "acme/model-x"}
            if state["pricing"] is not None:
                entry["pricing"] = state["pricing"]
            return {"data": [entry]}
        calls.append({"path": path, "payload": payload})
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(providers, "_openrouter_request", fake_request)
    monkeypatch.setattr(providers, "_openrouter_pricing_cache", {})
    monkeypatch.setattr(providers, "_openrouter_slug_cache", {}, raising=False)
    monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
    return calls, queue, state


def _request(seed=0):
    return providers.GenerationRequest(
        model_id="openrouter-x", instrument_id="udu", seed=seed, spec={}, prompt="p", prompt_sha256="0" * 64
    )


def _gen(budget=None):
    return providers.make_openrouter_generator("acme/model-x", budget=budget)


def test_actual_cost_tokens_and_response_id_land_in_trial_provenance(http, tmp_path):
    calls, queue, _ = http
    queue.append(_ok(0.0123))
    results = run_generation_batch(
        registry=REGISTRY, instrument_id="udu", seed=0, model_ids=["openrouter-x"], generator=_gen(), out_dir=tmp_path
    )
    assert results[0].status == "ok"
    assert calls[0]["payload"]["usage"] == {"include": True}
    assert "max_tokens" not in calls[0]["payload"]  # unbudgeted: request shape otherwise unchanged
    usage = json.loads(results[0].provenance_path.read_text(encoding="utf-8"))["usage"]
    assert usage == {"slug": "acme/model-x", "cost_usd": 0.0123, "response_id": "gen-1",
                     "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_cli_entrants_have_no_usage_block(tmp_path):
    results = run_generation_batch(
        registry=REGISTRY, instrument_id="udu", seed=0, model_ids=["stub-a"], generator=providers.make_stub_generator(), out_dir=tmp_path
    )
    assert "usage" not in json.loads(results[0].provenance_path.read_text(encoding="utf-8"))


def test_budgeted_request_carries_a_token_cap_and_a_provider_side_max_price(http, tmp_path):
    calls, queue, _ = http
    queue.append(_ok(0.001))
    _gen(MeteredBudget(5.0, tmp_path / "l.jsonl"))(_request())
    payload = calls[0]["payload"]
    assert payload["max_tokens"] == providers.DEFAULT_OPENROUTER_MAX_TOKENS
    assert payload["provider"]["max_price"] == {"prompt": 1.0, "completion": 2.0, "request": 0.0}  # token prices per million, request fee per call


def test_the_first_call_is_refused_when_its_maximum_could_exceed_the_cap(http, tmp_path):
    calls, queue, state = http
    state["pricing"] = EXPENSIVE  # bound ~ $0.50 per request
    queue.append(_ok(0.01))
    budget = MeteredBudget(0.40, tmp_path / "l.jsonl")
    with pytest.raises(BudgetExhausted, match="would exceed"):
        _gen(budget)(_request())
    assert calls == []  # nothing was sent
    assert budget.halt_reason and not budget.halt_call_made


def test_a_cheap_first_call_then_an_expensive_one_cannot_overshoot(http, tmp_path):
    calls, queue, state = http
    state["pricing"] = EXPENSIVE
    queue.append(_ok(0.45))
    budget = MeteredBudget(0.90, tmp_path / "l.jsonl")
    gen = _gen(budget)
    gen(_request(0))  # reserve ~0.5, settle 0.45
    with pytest.raises(BudgetExhausted):  # 0.45 + ~0.5 > 0.90
        gen(_request(1))
    assert len(calls) == 1 and budget.cumulative_usd == pytest.approx(0.45)


def test_unknown_pricing_refuses_dispatch(http, tmp_path):
    calls, queue, state = http
    state["pricing"] = None
    queue.append(_ok(0.01))
    budget = MeteredBudget(5.0)
    with pytest.raises(BudgetExhausted, match="no bounded maximum"):
        _gen(budget)(_request())
    assert calls == []


def test_the_request_fee_is_reserved_and_constrained_in_the_routing_request(http, tmp_path):
    calls, queue, state = http
    state["pricing"] = {"prompt": "0", "completion": "0", "request": "0.01"}
    queue.append(_ok(0.01))
    budget = MeteredBudget(5.0, tmp_path / "l.jsonl")
    _gen(budget)(_request())
    assert calls[0]["payload"]["provider"]["max_price"]["request"] == 0.01
    reserve = json.loads((tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert reserve["max_cost_usd"] == pytest.approx(0.01)


def test_a_settlement_above_its_reservation_halts_and_keeps_both_numbers(http, tmp_path):
    calls, queue, state = http
    state["pricing"] = {"prompt": "0", "completion": "0", "request": "0.01"}
    queue.append(_ok(20.0))  # a broken bound: the flat fee charged far more than listed
    ledger = tmp_path / "l.jsonl"
    budget = MeteredBudget(25.0, ledger)
    gen = _gen(budget)
    gen(_request(0))
    settle = json.loads(ledger.read_text(encoding="utf-8").splitlines()[1])
    assert settle["cost_usd"] == 20.0 and settle["reserved_max_usd"] == pytest.approx(0.01) and settle["exceeded_reservation"]
    assert budget.halt_reason and "exceeded its reserved maximum" in budget.halt_reason
    with pytest.raises(BudgetExhausted):
        gen(_request(1))
    assert len(calls) == 1


@pytest.mark.parametrize("pricing", [
    {"request": "0.01"},  # no token prices at all
    {"prompt": "0.000001"},  # completion missing
    {"completion": "0.000002"},  # prompt missing
    {"prompt": "0", "completion": "0", "request": "unknown"},
    {"prompt": "0", "completion": "0", "internal_reasoning": "n/a"},
    {"prompt": "-1", "completion": "0"},
    {"prompt": "nan", "completion": "0"},
    {"prompt": True, "completion": "0"},
])
def test_missing_or_invalid_billing_prices_refuse_dispatch(http, tmp_path, pricing):
    calls, queue, state = http
    state["pricing"] = pricing
    queue.append(_ok(0.01))
    with pytest.raises(BudgetExhausted, match="no bounded maximum"):
        _gen(MeteredBudget(5.0, tmp_path / "l.jsonl"))(_request())
    assert calls == []


def test_an_absent_optional_charge_is_zero_not_unknown():
    parsed = providers._parse_pricing({"prompt": "0.000001", "completion": "0.000002"})
    assert parsed == {"prompt": 1e-6, "completion": 2e-6, "request": 0.0, "internal_reasoning": 0.0}


def test_failed_request_makes_one_call_and_halts_a_budgeted_run(http, tmp_path):
    calls, queue, _ = http
    queue.append(RuntimeError("502"))
    budget = MeteredBudget(5.0, tmp_path / "l.jsonl")
    with pytest.raises(MeteredCostError, match="cost unknown"):
        _gen(budget)(_request())
    assert len(calls) == 1
    assert budget.halt_reason and budget.halt_call_made


def test_missing_cost_halts_and_is_ledgered_as_unknown(http, tmp_path):
    calls, queue, _ = http
    queue.append(_ok(None))
    ledger = tmp_path / "l.jsonl"
    budget = MeteredBudget(5.0, ledger)
    gen = _gen(budget)
    with pytest.raises(MeteredCostError, match="usable usage.cost"):
        gen(_request())
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["reserve", "settle"] and rows[1]["cost_usd"] is None
    with pytest.raises(BudgetExhausted):
        gen(_request(seed=1))
    assert len(calls) == 1
    # a fresh process reading the same ledger fails closed too
    assert MeteredBudget(5.0, ledger).halt_reason


@pytest.mark.parametrize("bad", [-0.01, float("nan"), float("inf"), "0.02", True])
def test_unreadable_cost_values_are_unknown(bad):
    assert providers._usage_cost({"usage": {"cost": bad}})[0] is None


@pytest.mark.parametrize("bad", [float("nan"), -50.0, True, "3", float("inf")])
def test_an_invalid_persisted_cost_fails_closed_and_the_row_is_kept(tmp_path, bad):
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps({"kind": "reserve", "id": "a", "model": "m", "max_cost_usd": 1.0}) + "\n"
                      + json.dumps({"kind": "settle", "id": "a", "model": "m", "cost_usd": bad}) + "\n", encoding="utf-8")
    before = ledger.read_text(encoding="utf-8")
    budget = MeteredBudget(19.0, ledger)
    assert budget.halt_reason
    with pytest.raises(BudgetExhausted):
        budget.reserve(model="m", max_cost_usd=0.01)
    assert ledger.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("bad", [float("nan"), -1.0, True, None])
def test_an_invalid_maximum_refuses_dispatch(tmp_path, bad):
    with pytest.raises(BudgetExhausted):
        MeteredBudget(5.0, tmp_path / "l.jsonl").reserve(model="m", max_cost_usd=bad)


def test_two_budget_instances_share_one_ledger_without_double_spending(tmp_path):
    ledger = tmp_path / "l.jsonl"
    first, second = MeteredBudget(1.0, ledger), MeteredBudget(1.0, ledger)
    rid = first.reserve(model="m", max_cost_usd=0.6)
    with pytest.raises(BudgetExhausted, match="outstanding"):
        second.reserve(model="m", max_cost_usd=0.6)  # the first call's reservation is visible
    first.settle(rid, model="m", cost_usd=0.5)
    assert second.cumulative_usd == pytest.approx(0.5)
    MeteredBudget(1.0, ledger).reserve(model="m", max_cost_usd=0.4)  # 0.5 settled + 0.4 fits (a new instance; a refusal halts the old one)
    with pytest.raises(BudgetExhausted):
        MeteredBudget(1.0, ledger).reserve(model="m", max_cost_usd=0.2)


def test_an_unsettled_reservation_keeps_counting_at_its_maximum(tmp_path):
    ledger = tmp_path / "l.jsonl"
    MeteredBudget(1.0, ledger).reserve(model="m", max_cost_usd=0.8)  # crash before settle
    with pytest.raises(BudgetExhausted):
        MeteredBudget(1.0, ledger).reserve(model="m", max_cost_usd=0.3)


def test_ledger_rows_identify_each_charged_call(http, tmp_path):
    _, queue, _ = http
    queue.append(_ok(0.002, rid="gen-abc"))
    ledger = tmp_path / "l.jsonl"
    _gen(MeteredBudget(5.0, ledger))(_request(seed=4))
    reserve, settle = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert reserve["instrument"] == "udu" and reserve["seed"] == 4 and reserve["entrant"] == "openrouter-x"
    assert settle["response_id"] == "gen-abc" and settle["id"] == reserve["id"]


def test_max_cost_must_be_positive_and_finite():
    for bad in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            MeteredBudget(bad)


def _config(n_seeds):
    return OrchestrationConfig(
        instrument_ids=("udu",), model_ids=("openrouter-x",), seeds=tuple(range(n_seeds)), max_attempts=2,
        model_providers={"openrouter-x": "openrouter"},
    )


def test_orchestrator_halts_leaves_unsent_trials_pending_and_gives_the_attempt_back(http, tmp_path):
    calls, queue, state = http
    state["pricing"] = EXPENSIVE
    queue.append(_ok(0.40))
    budget = MeteredBudget(1.2)
    gen = _gen(budget)

    def execute(trial):
        gen(_request(trial.seed))
        return {"status": "scored"}

    log = run_orchestration(config=_config(4), run_log_path=tmp_path / "run_log.json", execute_trial=execute, budget=budget)
    rows = {t["seed"]: t for t in log["trials"]}
    assert [rows[s]["status"] for s in (0, 1)] == ["scored", "scored"]
    assert rows[2]["status"] == "pending" and rows[2]["attempts"] == 0 and "not run" in rows[2]["error"]
    assert rows[3]["status"] == "pending" and rows[3]["attempts"] == 0
    assert len(calls) == 2 and budget.cumulative_usd == pytest.approx(0.80)


def test_orchestrator_keeps_the_attempt_when_the_halting_call_was_sent(http, tmp_path):
    _, queue, _ = http
    queue.append(_ok(None))
    budget = MeteredBudget(5.0)
    gen = _gen(budget)

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


def test_cli_stub_run_with_an_openrouter_id_needs_no_cap(tmp_path):
    result = CliRunner().invoke(
        app, ["arena", "run", "--run-dir", str(tmp_path / "r"), "--instruments", "ocarina", "--models", "openrouter-glm-5.2", "--stub"]
    )
    assert "--max-cost" not in result.output
