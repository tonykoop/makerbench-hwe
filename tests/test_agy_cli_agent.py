"""Offline unit tests for the Antigravity/Gemini CLI adapter.

Issue #12 pins the current honesty boundary: the `agy` CLI does not expose
per-run token counts to the harness, so rows must stay subscription_opaque
instead of inventing local-log or billing-derived usage.
"""

import importlib.util
from pathlib import Path

from makerbench.schema import TaskSpec

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "agy_cli_agent.py"


def _load_agent_module(monkeypatch=None, model="antigravity-gemini-default"):
    if monkeypatch is not None:
        monkeypatch.setenv("MAKERBENCH_MODEL", model)
    spec = importlib.util.spec_from_file_location("agy_cli_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agy = _load_agent_module()


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="vented_plate",
        seed=0,
        params={},
        brief="make a vented plate",
        units="mm",
        allowed_tools=[],
    )


def test_extract_scad_from_fenced_block():
    text = "Here:\n```openscad\ncube([1,2,3]);\n```\nDone."
    assert agy._extract_scad(text) == "cube([1,2,3]);"


def test_base_cmd_places_prompt_after_print_before_timeout(monkeypatch):
    monkeypatch.setattr(agy, "AGY_BIN", "agy-test")
    monkeypatch.setattr(agy, "AGY_ARGS", "--print --some-flag")
    monkeypatch.setattr(agy, "PRINT_TIMEOUT", "20m")

    cmd = agy._base_cmd("build a cube")

    assert cmd[:4] == ["agy-test", "--print", "--some-flag", "build a cube"]
    assert cmd[-2:] == ["--print-timeout", "20m"]


def test_usage_report_is_explicitly_subscription_opaque(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")

    usage = mod._usage_report()

    assert usage.source == "subscription_opaque"
    assert usage.provider == "google"
    assert usage.model == "antigravity-gemini-3.5-flash"
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.cached_input_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.total_tokens is None
    assert usage.measurement_authority is None
    assert usage.measurement_tool is None
    assert usage.measurement_source is None
    assert usage.estimated is False


def test_agent_keeps_agy_usage_opaque_and_cost_absent(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-default")
    monkeypatch.setattr(mod, "_call_agy", lambda prompt, retries=1: "```scad\ncube(1);\n```")

    attempt = mod.agent(_task(), track="blind", tools={}, perceive=None, budget=1)

    assert attempt.source == "cube(1);"
    assert attempt.usage is not None
    assert attempt.usage.source == "subscription_opaque"
    assert attempt.usage.provider == "google"
    assert attempt.usage.model == "antigravity-gemini-default"
    assert attempt.usage.total_tokens is None
    assert attempt.cost is None
    assert attempt.cost_usd is None
