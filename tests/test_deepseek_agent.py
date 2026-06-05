"""Offline unit tests for the direct DeepSeek API adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "deepseek_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("deepseek_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


deepseek = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert deepseek._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_joins_chat_completion_choices():
    payload = {
        "choices": [
            {"message": {"reasoning_content": "think", "content": "hello"}},
            {"message": {"content": "world"}},
        ]
    }
    assert deepseek._extract_text(payload) == "hello\nworld"


def test_usage_from_responses_style_payload_maps_usage():
    payload = {
        "model": "deepseek-v4-pro",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 380,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 500,
        },
    }
    usage = deepseek._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "deepseek"
    assert usage.model == "deepseek-v4-pro"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 380
    assert usage.reasoning_tokens == 80
    assert usage.cached_input_tokens == 20
    assert usage.total_tokens == 500


def test_usage_from_deepseek_chat_payload_tracks_cache_and_reasoning_detail():
    payload = {
        "model": "deepseek-v4-pro",
        "usage": {
            "prompt_tokens": 32,
            "completion_tokens": 119,
            "prompt_cache_hit_tokens": 8,
            "prompt_cache_miss_tokens": 24,
            "completion_tokens_details": {"reasoning_tokens": 110},
            "total_tokens": 151,
        },
    }
    usage = deepseek._usage_from_response(payload)
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
    usage = deepseek._usage_from_response(payload)
    assert usage is not None
    assert usage.output_tokens == 200
    assert usage.reasoning_tokens == 50
    assert usage.total_tokens == 300


def test_usage_from_response_returns_none_without_metadata():
    assert deepseek._usage_from_response({}) is None
    assert deepseek._usage_from_response({"usage": "nope"}) is None


def test_call_deepseek_requires_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY is not set"):
        deepseek._call_deepseek("hello")


def test_thinking_config_modes(monkeypatch):
    monkeypatch.setattr(deepseek, "THINKING_TYPE", "enabled")
    assert deepseek._thinking_config() == {"type": "enabled"}
    monkeypatch.setattr(deepseek, "THINKING_TYPE", "disabled")
    assert deepseek._thinking_config() == {"type": "disabled"}
    monkeypatch.setattr(deepseek, "THINKING_TYPE", "omitted")
    assert deepseek._thinking_config() is None


def test_thinking_config_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(deepseek, "THINKING_TYPE", "fast")
    with pytest.raises(RuntimeError, match="MAKERBENCH_THINKING_TYPE"):
        deepseek._thinking_config()


def test_reasoning_effort_modes(monkeypatch):
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "high")
    assert deepseek._reasoning_effort() == "high"
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "max")
    assert deepseek._reasoning_effort() == "max"
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "low")
    assert deepseek._reasoning_effort() == "low"
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "omitted")
    assert deepseek._reasoning_effort() is None


def test_reasoning_effort_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "turbo")
    with pytest.raises(RuntimeError, match="MAKERBENCH_REASONING_EFFORT"):
        deepseek._reasoning_effort()


def test_call_deepseek_uses_chat_completion_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl_1",
                "model": "deepseek-v4-pro",
                "choices": [{"message": {"content": "```scad\ncube(1);\n```"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setattr(deepseek.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(deepseek, "API_URL", "https://api.deepseek.com/chat/completions")
    monkeypatch.setattr(deepseek, "MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(deepseek, "THINKING_TYPE", "enabled")
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "high")

    text, raw = deepseek._call_deepseek("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["id"] == "chatcmpl_1"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer deepseek-test"
    assert captured["body"]["model"] == "deepseek-v4-pro"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1] == {"role": "user", "content": "build a cube"}
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["reasoning_effort"] == "high"
    assert captured["body"]["max_tokens"] == deepseek.MAX_OUTPUT_TOKENS
    assert captured["timeout"] == 900


def test_call_deepseek_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"error":{"message":"bad"}}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError(
            "https://api.deepseek.com/chat/completions",
            400,
            "Bad Request",
            {},
            ErrorBody(),
        )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setattr(deepseek.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="DeepSeek API error 400"):
        deepseek._call_deepseek("hello")


def test_agent_records_deepseek_trace_and_cost(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "chatcmpl_2",
                "model": "deepseek-v4-pro",
                "choices": [{"message": {"reasoning_content": "think", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "prompt_cache_hit_tokens": 100_000,
                    "completion_tokens_details": {"reasoning_tokens": 250_000},
                },
            },
        )

    monkeypatch.setattr(deepseek, "_call_deepseek", fake_call)
    monkeypatch.setattr(deepseek, "THINKING_TYPE", "enabled")
    monkeypatch.setattr(deepseek, "REASONING_EFFORT", "high")
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = deepseek.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "deepseek"
    assert attempt.usage.reasoning_tokens == 250_000
    assert attempt.usage.output_tokens == 1_000_000
    assert attempt.cost is not None
    assert attempt.cost.output_cost_usd == 0.87
    assert attempt.trace[0]["api_surface"] == "deepseek_chat_completions"
    assert attempt.trace[0]["endpoint"] == "https://api.deepseek.com/chat/completions"
    assert attempt.trace[0]["thinking_type"] == "enabled"
    assert attempt.trace[0]["reasoning_effort"] == "high"
    assert attempt.trace[0]["model_family"] == "DeepSeek-V4-Pro"
    assert attempt.trace[0]["legacy_deepseek_alias"] is False
    assert attempt.trace[0]["reasoning_content_chars"] == 5
    assert attempt.trace[0]["gateway"] == "native_deepseek"
    assert calls


def test_pricing_resolves_for_deepseek_v4_models_only():
    pricing = find_pricing("deepseek", "deepseek-v4-pro")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#deepseek-v4-pro")
    assert entry["input_usd_per_1m_tokens"] == 0.435
    assert entry["cached_input_usd_per_1m_tokens"] == 0.003625
    assert entry["output_usd_per_1m_tokens"] == 0.87

    flash_pricing = find_pricing("deepseek", "deepseek-v4-flash")
    assert flash_pricing is not None
    flash_entry, flash_ref = flash_pricing
    assert flash_ref.endswith("#deepseek-v4-flash")
    assert flash_entry["input_usd_per_1m_tokens"] == 0.14
    assert flash_entry["cached_input_usd_per_1m_tokens"] == 0.0028
    assert flash_entry["output_usd_per_1m_tokens"] == 0.28

    assert find_pricing("deepseek", "deepseek-chat") is None
    assert find_pricing("deepseek", "deepseek-reasoner") is None


def test_reasoning_tokens_are_tracked_inside_deepseek_output_bucket():
    payload = {
        "model": "deepseek-v4-pro",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 1_000_000,
            "completion_tokens_details": {"reasoning_tokens": 250_000},
            "total_tokens": 1_000_000,
        },
    }
    usage = deepseek._usage_from_response(payload)
    assert usage.output_tokens == 1_000_000
    assert usage.reasoning_tokens == 250_000
    cost = estimate_cost(usage)
    assert cost.output_cost_usd == 0.87
    assert cost.total_cost_usd == 0.87


def test_estimate_cost_for_measured_deepseek_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.total_cost_usd is not None
    assert cost.input_cost_usd == 0.3915
    assert cost.cached_input_cost_usd == 0.0003625
    assert cost.output_cost_usd == 0.87
    assert cost.total_cost_usd == 1.2618625
