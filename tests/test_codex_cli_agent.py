"""Offline unit tests for the Codex CLI adapter (no `codex` process).

Covers the local-log usage capture added for #6: subscription Codex runs now
record real token counts from `codex exec --json` (turn.completed `usage` events)
plus an API-equivalent cost, instead of an opaque placeholder. Codex is
subscription auth with no per-run bill, so usage is `local_log` (not `measured`)
and the cost is API-equivalent only.
"""

import importlib.util
from pathlib import Path

from makerbench.schema import TaskSpec

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "codex_cli_agent.py"


def _load_agent_module(monkeypatch=None, model="gpt-5.4"):
    if monkeypatch is not None:
        monkeypatch.setenv("MAKERBENCH_MODEL", model)
    spec = importlib.util.spec_from_file_location("codex_cli_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Module loaded with the default env model; per-test reloads override MODEL.
codex = _load_agent_module()


def _jsonl(*lines: str) -> str:
    return "\n".join(lines)


# A realistic `codex exec --json` JSONL stream (trimmed).
_STREAM = _jsonl(
    '{"type":"thread.started"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message",'
    '"text":"```scad\\ncube([1,2,3]);\\n```"}}',
    '{"type":"turn.completed","usage":{"input_tokens":1000,'
    '"cached_input_tokens":400,"output_tokens":200,"reasoning_output_tokens":50}}',
)


def test_extract_scad_from_fenced_block():
    assert codex._extract_scad("x\n```scad\ncube([1,2,3]);\n```\ny") == "cube([1,2,3]);"


def test_parse_events_pulls_message_and_usage():
    message, usages = codex._parse_events(_STREAM)
    assert message == "```scad\ncube([1,2,3]);\n```"
    assert usages == [
        {"input_tokens": 1000, "cached_input_tokens": 400,
         "output_tokens": 200, "reasoning_output_tokens": 50}
    ]


def test_parse_events_ignores_non_json_and_keeps_last_message():
    stream = _jsonl(
        "not json at all",
        '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
        '{"type":"item.completed","item":{"type":"reasoning","text":"ignored"}}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}',
    )
    message, usages = codex._parse_events(stream)
    assert message == "final"
    assert usages == []


def test_accumulate_and_report_sums_local_log_usage():
    acc = codex._new_usage_acc()
    codex._accumulate_usage(acc, {"input_tokens": 1000, "cached_input_tokens": 400,
                                  "output_tokens": 200, "reasoning_output_tokens": 50})
    codex._accumulate_usage(acc, {"input_tokens": 30, "cached_input_tokens": 0,
                                  "output_tokens": 12, "reasoning_output_tokens": 3})
    report = codex._usage_report(acc)
    assert report.source == "local_log"
    assert report.provider == "openai"
    assert report.input_tokens == 1030
    assert report.output_tokens == 212
    assert report.cached_input_tokens == 400
    assert report.reasoning_tokens == 53
    # total = input + output (cached is a subset of input, reasoning a subset of output)
    assert report.total_tokens == 1030 + 212
    assert report.measurement_authority == "local_log"
    assert report.measurement_tool == "codex_cli_json"
    assert report.measurement_source == "codex"
    assert report.estimated is True


def test_usage_report_falls_back_to_opaque_without_usage():
    # An older CLI without --json yields no turn.completed usage -> honest opaque.
    report = codex._usage_report(codex._new_usage_acc())
    assert report.source == "subscription_opaque"
    assert report.input_tokens is None
    assert report.total_tokens is None


def test_cost_report_prices_api_equivalent_for_priced_model(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="gpt-5.4")
    acc = mod._new_usage_acc()
    mod._accumulate_usage(acc, {"input_tokens": 1000, "cached_input_tokens": 400,
                                "output_tokens": 200, "reasoning_output_tokens": 50})
    usage = mod._usage_report(acc)
    cost = mod._cost_report(usage)
    assert cost is not None
    assert cost.source == "not_available"      # never an actual bill
    assert cost.total_cost_usd is None
    assert cost.api_equivalent_usd is not None and cost.api_equivalent_usd > 0


def test_cost_report_none_for_unpriced_model(monkeypatch):
    # A model with no pricing row surfaces tokens but invents no cost.
    mod = _load_agent_module(monkeypatch, model="gpt-5.4-mini")
    acc = mod._new_usage_acc()
    mod._accumulate_usage(acc, {"input_tokens": 1000, "output_tokens": 200})
    usage = mod._usage_report(acc)
    assert usage.source == "local_log"
    assert mod._cost_report(usage) is None


def test_cost_report_none_for_opaque_usage():
    opaque = codex._usage_report(codex._new_usage_acc())
    assert codex._cost_report(opaque) is None


def test_agent_flows_local_log_usage_and_cost(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="gpt-5.4")
    monkeypatch.setattr(mod, "_call_codex",
                        lambda prompt, retries=1: mod._parse_events(_STREAM))
    spec = TaskSpec(
        task_id="vented_plate", seed=0, params={}, brief="make a plate",
        units="mm", allowed_tools=[],
    )
    attempt = mod.agent(spec, track="blind", tools={}, perceive=None, budget=1)
    assert attempt.source == "cube([1,2,3]);"
    assert attempt.usage.source == "local_log"
    assert attempt.usage.input_tokens == 1000
    assert attempt.usage.output_tokens == 200
    # Subscription cost is API-equivalent only: legacy cost_usd null, figure in cost.
    assert attempt.cost_usd is None
    assert attempt.cost is not None
    assert attempt.cost.source == "not_available"
    assert attempt.cost.total_cost_usd is None
    assert attempt.cost.api_equivalent_usd is not None and attempt.cost.api_equivalent_usd > 0
