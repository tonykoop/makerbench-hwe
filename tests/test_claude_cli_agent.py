"""Offline unit tests for the Claude Code CLI adapter (no `claude` process).

Covers the measured-usage capture added in #6: subscription Claude runs now
record real token counts + an API-equivalent cost from `claude -p
--output-format json`, instead of an opaque placeholder.
"""

import importlib.util
from pathlib import Path

from makerbench.schema import TaskSpec

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "claude_cli_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("claude_cli_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


claude = _load_agent_module()


# A realistic `claude -p --output-format json` envelope (trimmed).
def _payload(input_tokens, output_tokens, cache_read, cache_creation, cost, model):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "```scad\ncube([1,2,3]);\n```",
        "total_cost_usd": cost,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
        "modelUsage": {model: {"costUSD": cost}},
    }


def test_extract_scad_from_fenced_block():
    assert claude._extract_scad("x\n```scad\ncube([1,2,3]);\n```\ny") == "cube([1,2,3]);"


def test_extract_scad_falls_back_to_raw_text():
    assert claude._extract_scad("cube([4,5,6]);") == "cube([4,5,6]);"


def test_accumulate_and_report_sums_measured_usage():
    acc = claude._new_usage_acc()
    claude._accumulate_usage(acc, _payload(10, 42, 17792, 7317, 0.0116, "claude-haiku-4-5-20251001"))
    claude._accumulate_usage(acc, _payload(5, 30, 100, 0, 0.0040, "claude-haiku-4-5-20251001"))
    report = claude._usage_report(acc)
    assert report.source == "measured"
    assert report.provider == "anthropic"
    assert report.model == "claude-haiku-4-5-20251001"
    assert report.input_tokens == 15
    assert report.output_tokens == 72
    assert report.cached_input_tokens == 17792 + 7317 + 100  # cache read + creation, summed
    assert report.total_tokens == 15 + 72 + (17792 + 7317 + 100)
    assert report.measurement_authority == "api_billing"
    assert report.measurement_tool == "claude_cli_json"
    assert round(acc["cost"], 4) == 0.0156


def test_usage_report_falls_back_to_opaque_without_json():
    # An older CLI emitting plain text yields no usage -> honest opaque, not zeros.
    acc = claude._new_usage_acc()
    claude._accumulate_usage(acc, None)
    report = claude._usage_report(acc)
    assert report.source == "subscription_opaque"
    assert report.input_tokens is None
    assert report.total_tokens is None


def test_agent_flows_measured_usage_and_cost(monkeypatch):
    payload = _payload(120, 300, 5000, 200, 0.0521, "claude-sonnet-4-6")
    monkeypatch.setattr(claude, "_call_claude", lambda prompt, retries=1: (payload["result"], payload))
    spec = TaskSpec(
        task_id="vented_plate", seed=0, params={}, brief="make a plate",
        units="mm", allowed_tools=[],
    )
    attempt = claude.agent(spec, track="blind", tools={}, perceive=None, budget=1)
    assert attempt.source == "cube([1,2,3]);"
    assert attempt.usage.source == "measured"
    assert attempt.usage.input_tokens == 120
    assert attempt.usage.output_tokens == 300
    assert attempt.usage.model == "claude-sonnet-4-6"
    # Subscription cost is an API-equivalent estimate, never an actual bill:
    # legacy cost_usd stays null; the figure rides in cost.api_equivalent_usd.
    assert attempt.cost_usd is None
    assert attempt.cost is not None
    assert attempt.cost.source == "not_available"
    assert attempt.cost.total_cost_usd is None
    assert attempt.cost.api_equivalent_usd == 0.0521


def test_cost_report_is_none_without_usage():
    # No parseable usage -> no cost figure at all (not a zero bill).
    assert claude._cost_report(claude._new_usage_acc()) is None
