"""Offline unit tests for the direct Moonshot/Kimi API adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "kimi_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("kimi_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kimi = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert kimi._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_joins_chat_completion_choices():
    payload = {
        "choices": [
            {"message": {"reasoning_content": "think", "content": "hello"}},
            {"message": {"content": "world"}},
        ]
    }
    assert kimi._extract_text(payload) == "hello\nworld"


def test_usage_from_responses_style_payload_maps_usage():
    payload = {
        "model": "kimi-k2.6",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 380,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 500,
        },
    }
    usage = kimi._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "moonshot"
    assert usage.model == "kimi-k2.6"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 380
    assert usage.reasoning_tokens == 80
    assert usage.cached_input_tokens == 20
    assert usage.total_tokens == 500


def test_usage_from_chat_style_payload_bills_reasoning_as_output():
    payload = {
        "model": "kimi-k2.6",
        "usage": {
            "prompt_tokens": 32,
            "completion_tokens": 9,
            "prompt_tokens_details": {"cached_tokens": 8},
            "completion_tokens_details": {"reasoning_tokens": 110},
            "total_tokens": 151,
        },
    }
    usage = kimi._usage_from_response(payload)
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
    usage = kimi._usage_from_response(payload)
    assert usage is not None
    assert usage.output_tokens == 250
    assert usage.reasoning_tokens == 50
    assert usage.total_tokens == 350


def test_usage_from_response_returns_none_without_metadata():
    assert kimi._usage_from_response({}) is None
    assert kimi._usage_from_response({"usage": "nope"}) is None


def test_call_kimi_requires_key(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Neither MOONSHOT_API_KEY nor KIMI_API_KEY is set"):
        kimi._call_kimi("hello")


def test_thinking_config_modes(monkeypatch):
    monkeypatch.setattr(kimi, "THINKING_TYPE", "enabled")
    assert kimi._thinking_config() == {"type": "enabled"}
    monkeypatch.setattr(kimi, "THINKING_TYPE", "disabled")
    assert kimi._thinking_config() == {"type": "disabled"}
    monkeypatch.setattr(kimi, "THINKING_TYPE", "omitted")
    assert kimi._thinking_config() is None


def test_thinking_config_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(kimi, "THINKING_TYPE", "fast")
    with pytest.raises(RuntimeError, match="MAKERBENCH_THINKING_TYPE"):
        kimi._thinking_config()


def test_call_kimi_uses_moonshot_chat_completion_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl_1",
                "model": "kimi-k2.6",
                "choices": [{"message": {"content": "```scad\ncube(1);\n```"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("MOONSHOT_API_KEY", "moon-test")
    monkeypatch.setattr(kimi.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(kimi, "API_URL", "https://api.moonshot.ai/v1/chat/completions")
    monkeypatch.setattr(kimi, "MODEL", "kimi-k2.6")
    monkeypatch.setattr(kimi, "THINKING_TYPE", "enabled")

    text, raw = kimi._call_kimi("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["id"] == "chatcmpl_1"
    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer moon-test"
    assert captured["body"]["model"] == "kimi-k2.6"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1] == {"role": "user", "content": "build a cube"}
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["max_tokens"] == kimi.MAX_OUTPUT_TOKENS
    assert captured["timeout"] == 900


def test_call_kimi_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"error":{"message":"bad"}}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError(
            "https://api.moonshot.ai/v1/chat/completions",
            400,
            "Bad Request",
            {},
            ErrorBody(),
        )

    monkeypatch.setenv("MOONSHOT_API_KEY", "moon-test")
    monkeypatch.setattr(kimi.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Moonshot API error 400"):
        kimi._call_kimi("hello")


def test_agent_records_moonshot_trace_and_cost(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "chatcmpl_2",
                "model": "kimi-k2.6",
                "choices": [{"message": {"reasoning_content": "think", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "prompt_tokens_details": {"cached_tokens": 100_000},
                    "completion_tokens_details": {"reasoning_tokens": 250_000},
                },
            },
        )

    monkeypatch.setattr(kimi, "_call_kimi", fake_call)
    monkeypatch.setattr(kimi, "THINKING_TYPE", "enabled")
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = kimi.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "moonshot"
    assert attempt.usage.reasoning_tokens == 250_000
    assert attempt.usage.output_tokens == 1_250_000
    assert attempt.cost is not None
    assert attempt.cost.output_cost_usd == 5.0
    assert attempt.trace[0]["api_surface"] == "moonshot_chat_completions"
    assert attempt.trace[0]["endpoint"] == "https://api.moonshot.ai/v1/chat/completions"
    assert attempt.trace[0]["thinking_type"] == "enabled"
    assert attempt.trace[0]["model_family"] == "kimi-k2.6"
    assert attempt.trace[0]["deprecated_kimi_k2_series"] is False
    assert attempt.trace[0]["reasoning_content_chars"] == 5
    assert attempt.trace[0]["gateway"] == "native_moonshot"
    assert calls


def test_pricing_resolves_for_kimi_k26_only():
    pricing = find_pricing("moonshot", "kimi-k2.6")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#kimi-k2.6")
    assert entry["input_usd_per_1m_tokens"] == 0.95
    assert entry["cached_input_usd_per_1m_tokens"] == 0.16
    assert entry["output_usd_per_1m_tokens"] == 4.0

    assert find_pricing("moonshot", "kimi-k2") is None
    assert find_pricing("moonshot", "kimi-k2-thinking") is None


def test_reasoning_tokens_are_billed_as_output_for_moonshot():
    payload = {
        "model": "kimi-k2.6",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 1_000_000,
            "completion_tokens_details": {"reasoning_tokens": 1_000_000},
            "total_tokens": 2_000_000,
        },
    }
    usage = kimi._usage_from_response(payload)
    assert usage.output_tokens == 2_000_000
    assert usage.reasoning_tokens == 1_000_000
    cost = estimate_cost(usage)
    assert cost.output_cost_usd == 8.0
    assert cost.total_cost_usd == 8.0


def test_estimate_cost_for_measured_moonshot_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="moonshot",
            model="kimi-k2.6",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.total_cost_usd is not None
    assert cost.input_cost_usd == 0.855
    assert cost.cached_input_cost_usd == 0.016
    assert cost.output_cost_usd == 4.0
