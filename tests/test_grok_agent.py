"""Offline unit tests for the direct xAI Grok API adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "grok_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("grok_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grok = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert grok._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_prefers_output_text():
    assert grok._extract_text({"output_text": "cube([1,2,3]);"}) == "cube([1,2,3]);"


def test_extract_text_joins_response_content():
    payload = {
        "output": [
            {"content": [{"type": "output_text", "text": "hello"}, {"type": "text", "text": "world"}]}
        ]
    }
    assert grok._extract_text(payload) == "hello\nworld"


def test_usage_from_responses_payload_maps_usage():
    payload = {
        "model": "grok-4.3",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 380,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 500,
        },
    }
    usage = grok._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "xai"
    assert usage.model == "grok-4.3"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 380
    assert usage.reasoning_tokens == 80
    assert usage.cached_input_tokens == 20
    assert usage.total_tokens == 500


def test_usage_from_chat_style_payload_bills_reasoning_as_output():
    payload = {
        "model": "grok-4.3",
        "usage": {
            "prompt_tokens": 32,
            "completion_tokens": 9,
            "prompt_tokens_details": {"cached_tokens": 8},
            "completion_tokens_details": {"reasoning_tokens": 110},
            "total_tokens": 151,
        },
    }
    usage = grok._usage_from_response(payload)
    assert usage is not None
    assert usage.input_tokens == 32
    assert usage.output_tokens == 119
    assert usage.reasoning_tokens == 110
    assert usage.cached_input_tokens == 8
    assert usage.total_tokens == 151


def test_usage_from_response_derives_total_when_absent():
    payload = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 200,
            "completion_tokens_details": {"reasoning_tokens": 50},
        }
    }
    usage = grok._usage_from_response(payload)
    assert usage is not None
    assert usage.output_tokens == 250
    assert usage.reasoning_tokens == 50
    assert usage.total_tokens == 350


def test_usage_from_response_returns_none_without_metadata():
    assert grok._usage_from_response({}) is None
    assert grok._usage_from_response({"usage": "nope"}) is None


def test_call_grok_requires_xai_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="XAI_API_KEY is not set"):
        grok._call_grok("hello")


def test_call_grok_uses_xai_responses_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "resp_1",
                "model": "grok-4.3",
                "output_text": "```scad\ncube(1);\n```",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setattr(grok.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(grok, "API_URL", "https://api.x.ai/v1/responses")
    monkeypatch.setattr(grok, "MODEL", "grok-4.3")
    monkeypatch.setattr(grok, "REASONING_EFFORT", "high")

    text, raw = grok._call_grok("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["id"] == "resp_1"
    assert captured["url"] == "https://api.x.ai/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer xai-test"
    assert captured["body"]["model"] == "grok-4.3"
    assert captured["body"]["reasoning"] == {"effort": "high"}
    assert captured["timeout"] == 900


def test_call_grok_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"error":"bad"}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError("https://api.x.ai/v1/responses", 400, "Bad Request", {}, ErrorBody())

    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setattr(grok.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="xAI API error 400"):
        grok._call_grok("hello")


def test_agent_records_xai_trace_and_cost(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "resp_2",
                "model": "grok-4.3",
                "usage": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "input_tokens_details": {"cached_tokens": 100_000},
                    "output_tokens_details": {"reasoning_tokens": 250_000},
                },
            },
        )

    monkeypatch.setattr(grok, "_call_grok", fake_call)
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = grok.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "xai"
    assert attempt.usage.reasoning_tokens == 250_000
    assert attempt.cost is not None
    assert attempt.cost.output_cost_usd == 2.5
    assert attempt.trace[0]["api_surface"] == "xai_responses_api"
    assert attempt.trace[0]["endpoint"] == "https://api.x.ai/v1/responses"
    assert attempt.trace[0]["image_perception_support"] == "not_enabled_in_adapter"
    assert calls


def test_pricing_resolves_for_default_and_coding_alias_models():
    pricing = find_pricing("xai", "grok-4.3")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#grok-4.3")
    assert entry["input_usd_per_1m_tokens"] == 1.25

    alias_pricing = find_pricing("xai", "grok-code-fast-1")
    assert alias_pricing is not None
    alias_entry, alias_ref = alias_pricing
    assert alias_ref.endswith("#grok-code-fast-1")
    assert alias_entry["output_usd_per_1m_tokens"] == 2.0


def test_reasoning_tokens_are_billed_as_output_for_xai():
    payload = {
        "model": "grok-4.3",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 1_000_000,
            "completion_tokens_details": {"reasoning_tokens": 1_000_000},
            "total_tokens": 2_000_000,
        },
    }
    usage = grok._usage_from_response(payload)
    assert usage.output_tokens == 2_000_000
    assert usage.reasoning_tokens == 1_000_000
    cost = estimate_cost(usage)
    assert cost.output_cost_usd == 5.0
    assert cost.total_cost_usd == 5.0


def test_estimate_cost_for_measured_xai_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="xai",
            model="grok-4.3",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.total_cost_usd is not None
    assert cost.input_cost_usd == 1.125
    assert cost.cached_input_cost_usd == 0.02
    assert cost.output_cost_usd == 2.5
