"""Tests for Code-CAD Arena DoE orchestration runner (#426)."""

import json

from makerbench import code_cad_orchestrator as orch


def _config(**overrides):
    values = {
        "instrument_ids": ("lyre", "kora"),
        "model_ids": ("openai:gpt-5.5", "anthropic:sonnet"),
        "seeds": (0, 1),
        "reps": 2,
        "max_attempts": 1,
    }
    values.update(overrides)
    return orch.OrchestrationConfig(**values)


def test_trial_matrix_is_config_driven_and_deterministic():
    trials = orch.build_trial_matrix(_config())

    assert len(trials) == 16
    assert trials[0].trial_id == "lyre__seed0__rep0__openai-gpt-5.5"
    assert trials[0].instrument_id == "lyre"
    assert trials[0].model_id == "openai:gpt-5.5"
    assert trials[-1].trial_id == "kora__seed1__rep1__anthropic-sonnet"


def test_run_log_is_resumable_and_idempotent(tmp_path):
    config = _config(instrument_ids=("lyre",), model_ids=("gpt",), seeds=(0,), reps=1)
    calls = []

    def execute(trial):
        calls.append(trial.trial_id)
        return {"status": "ok", "artifact": f"{trial.trial_id}.scad"}

    path = tmp_path / "run.json"
    first = orch.run_orchestration(config=config, run_log_path=path, execute_trial=execute)
    second = orch.run_orchestration(config=config, run_log_path=path, execute_trial=execute)

    assert calls == ["lyre__seed0__rep0__gpt"]
    assert first["summary"]["counts"] == {"ok": 1}
    assert second["summary"]["counts"] == {"ok": 1}
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == orch.SCHEMA


def test_errors_retry_until_max_attempts(tmp_path):
    config = _config(
        instrument_ids=("lyre",),
        model_ids=("gpt",),
        seeds=(0,),
        reps=1,
        max_attempts=2,
    )
    calls = []

    def first_fails_then_ok(trial):
        calls.append(trial.trial_id)
        if len(calls) == 1:
            raise RuntimeError("provider 500")
        return {"status": "ok"}

    path = tmp_path / "run.json"
    failed = orch.run_orchestration(
        config=config,
        run_log_path=path,
        execute_trial=first_fails_then_ok,
    )
    recovered = orch.run_orchestration(
        config=config,
        run_log_path=path,
        execute_trial=first_fails_then_ok,
    )

    assert failed["summary"]["counts"] == {"error": 1}
    assert recovered["summary"]["counts"] == {"ok": 1}
    assert recovered["trials"][0]["attempts"] == 2


def test_provider_rate_limits_use_injected_sleep(tmp_path):
    config = _config(
        instrument_ids=("lyre",),
        model_ids=("a", "b"),
        seeds=(0,),
        reps=1,
        model_providers={"a": "shared", "b": "shared"},
        provider_rate_limits_s={"shared": 5.0},
    )
    slept = []
    now = {"t": 10.0}

    def clock():
        return now["t"]

    def sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    log = orch.run_orchestration(
        config=config,
        run_log_path=tmp_path / "run.json",
        execute_trial=lambda trial: {"status": "ok"},
        clock_fn=clock,
        sleep_fn=sleep,
    )

    assert slept == [5.0]
    assert log["trials"][1]["rate_limit_wait_s"] == 5.0


def test_doc_describes_manifest_consumers():
    text = open("docs/CODE_CAD_ORCHESTRATION.md", encoding="utf-8").read()
    assert "S3/S4/S6" in text
    assert "resumable" in text
