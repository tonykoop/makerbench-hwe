"""Offline unit tests for the direct Gemini API adapter (no network calls)."""

import importlib.util
from pathlib import Path

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "gemini_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("gemini_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gemini = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert gemini._extract_scad(text) == "cube([1,2,3]);"


def test_extract_scad_falls_back_to_raw_text():
    text = "cube([4,5,6]);"
    assert gemini._extract_scad(text) == "cube([4,5,6]);"


def test_extract_scad_tolerates_unclosed_fence():
    # Thinking-model output truncated at maxOutputTokens: opening fence, no close.
    text = "thought\n\n```openscad\nplate_length = 90;\ncube([1,2,3]);"
    assert gemini._extract_scad(text) == "plate_length = 90;\ncube([1,2,3]);"


def test_extract_text_joins_candidate_parts():
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}
        ]
    }
    assert gemini._extract_text(payload) == "hello \nworld"


def test_extract_text_handles_missing_candidates():
    assert gemini._extract_text({}) == ""
    assert gemini._extract_text({"candidates": [{}]}) == ""


def test_usage_from_response_maps_usage_metadata():
    payload = {
        "modelVersion": "gemini-3.5-flash",
        "usageMetadata": {
            "promptTokenCount": 120,
            "candidatesTokenCount": 300,
            "thoughtsTokenCount": 80,
            "cachedContentTokenCount": 20,
            "totalTokenCount": 500,
        },
    }
    usage = gemini._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "google"
    assert usage.model == "gemini-3.5-flash"
    assert usage.input_tokens == 120
    # output_tokens is the total billable output: answer (300) + thinking (80).
    assert usage.output_tokens == 380
    assert usage.reasoning_tokens == 80
    assert usage.cached_input_tokens == 20
    assert usage.total_tokens == 500


def test_usage_from_response_derives_total_when_absent():
    payload = {
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 200,
            "thoughtsTokenCount": 50,
        }
    }
    usage = gemini._usage_from_response(payload)
    assert usage is not None
    # answer (200) + thinking (50) billable output; total = prompt + output.
    assert usage.output_tokens == 250
    assert usage.reasoning_tokens == 50
    assert usage.total_tokens == 350


def test_usage_from_response_returns_none_without_metadata():
    assert gemini._usage_from_response({}) is None
    assert gemini._usage_from_response({"usageMetadata": "nope"}) is None


def test_thinking_config_prefers_level(monkeypatch):
    monkeypatch.setattr(gemini, "THINKING_LEVEL", "low")
    monkeypatch.setattr(gemini, "THINKING_BUDGET", "")
    assert gemini._thinking_config() == {"thinkingLevel": "low"}


def test_thinking_config_uses_budget_when_no_level(monkeypatch):
    monkeypatch.setattr(gemini, "THINKING_LEVEL", "")
    monkeypatch.setattr(gemini, "THINKING_BUDGET", "1024")
    assert gemini._thinking_config() == {"thinkingBudget": 1024}


def test_thinking_config_omitted_when_unset(monkeypatch):
    monkeypatch.setattr(gemini, "THINKING_LEVEL", "")
    monkeypatch.setattr(gemini, "THINKING_BUDGET", "")
    assert gemini._thinking_config() is None


def test_pricing_resolves_for_default_model():
    pricing = find_pricing("google", "gemini-3.5-flash")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#gemini-3.5-flash")
    assert entry["input_usd_per_1m_tokens"] == 1.5


def test_thinking_tokens_are_billed_as_output():
    # A reasoning-heavy response: 1M answer tokens + 1M thinking tokens. Gemini
    # bills thinking at the output rate, so all 2M must be charged as output.
    payload = {
        "modelVersion": "gemini-3.5-flash",
        "usageMetadata": {
            "promptTokenCount": 0,
            "candidatesTokenCount": 1_000_000,
            "thoughtsTokenCount": 1_000_000,
            "totalTokenCount": 2_000_000,
        },
    }
    usage = gemini._usage_from_response(payload)
    assert usage.output_tokens == 2_000_000
    assert usage.reasoning_tokens == 1_000_000
    cost = estimate_cost(usage)
    assert cost.output_cost_usd == 18.0  # 2M output @ $9/1M, thinking included
    assert cost.total_cost_usd == 18.0


def test_estimate_cost_for_measured_gemini_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="google",
            model="gemini-3.5-flash",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.total_cost_usd is not None
    # billable input = 0.9M @ $1.5 + cached 0.1M @ $0.15 + output 1M @ $9.0
    assert cost.input_cost_usd == 1.35
    assert cost.cached_input_cost_usd == 0.015
    assert cost.output_cost_usd == 9.0
