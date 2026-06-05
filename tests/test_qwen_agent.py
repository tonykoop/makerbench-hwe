"""Offline unit tests for the direct Qwen/DashScope API adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "qwen_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("qwen_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qwen = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert qwen._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_joins_chat_completion_choices_without_reasoning():
    payload = {
        "choices": [
            {"message": {"reasoning_content": "think", "content": "hello"}},
            {"message": {"content": "world"}},
        ]
    }
    assert qwen._extract_text(payload) == "hello\nworld"


def test_usage_from_responses_style_payload_maps_usage():
    payload = {
        "model": "qwen3.7-max",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 380,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 500,
        },
    }
    usage = qwen._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "qwen"
    assert usage.model == "qwen3.7-max"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 380
    assert usage.reasoning_tokens == 80
    assert usage.cached_input_tokens == 20
    assert usage.total_tokens == 500


def test_usage_from_qwen_chat_payload_tracks_cache_and_reasoning_detail():
    payload = {
        "model": "qwen3.7-max",
        "usage": {
            "prompt_tokens": 32,
            "completion_tokens": 119,
            "prompt_tokens_details": {"cached_tokens": 8},
            "completion_tokens_details": {
                "reasoning_tokens": 110,
                "text_tokens": 9,
            },
            "total_tokens": 151,
        },
    }
    usage = qwen._usage_from_response(payload)
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
    usage = qwen._usage_from_response(payload)
    assert usage is not None
    assert usage.output_tokens == 200
    assert usage.reasoning_tokens == 50
    assert usage.total_tokens == 300


def test_usage_from_response_returns_none_without_metadata():
    assert qwen._usage_from_response({}) is None
    assert qwen._usage_from_response({"usage": "nope"}) is None


def test_call_qwen_requires_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY or QWEN_API_KEY is not set"):
        qwen._call_qwen("hello")


def test_qwen_extra_body_modes(monkeypatch):
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "true")
    monkeypatch.setattr(qwen, "THINKING_BUDGET", "81920")
    monkeypatch.setattr(qwen, "PRESERVE_THINKING", "false")
    assert qwen._qwen_extra_body() == {
        "enable_thinking": True,
        "thinking_budget": 81920,
        "preserve_thinking": False,
    }
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "disabled")
    monkeypatch.setattr(qwen, "THINKING_BUDGET", "omitted")
    monkeypatch.setattr(qwen, "PRESERVE_THINKING", "omitted")
    assert qwen._qwen_extra_body() == {"enable_thinking": False}
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "omitted")
    assert qwen._qwen_extra_body() == {}


def test_qwen_extra_body_rejects_unknown_modes(monkeypatch):
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "maybe")
    with pytest.raises(RuntimeError, match="MAKERBENCH_ENABLE_THINKING"):
        qwen._qwen_extra_body()
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "true")
    monkeypatch.setattr(qwen, "THINKING_BUDGET", "lots")
    with pytest.raises(RuntimeError, match="MAKERBENCH_THINKING_BUDGET"):
        qwen._qwen_extra_body()


def test_call_qwen_uses_dashscope_chat_completion_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl_1",
                "model": "qwen3.7-max",
                "choices": [{"message": {"content": "```scad\ncube(1);\n```"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    monkeypatch.setattr(qwen.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        qwen,
        "API_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    monkeypatch.setattr(qwen, "MODEL", "qwen3.7-max")
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "true")
    monkeypatch.setattr(qwen, "THINKING_BUDGET", "81920")
    monkeypatch.setattr(qwen, "PRESERVE_THINKING", "omitted")

    text, raw = qwen._call_qwen("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["id"] == "chatcmpl_1"
    assert captured["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer dashscope-test"
    assert captured["body"]["model"] == "qwen3.7-max"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1] == {"role": "user", "content": "build a cube"}
    assert captured["body"]["enable_thinking"] is True
    assert captured["body"]["thinking_budget"] == 81920
    assert captured["body"]["max_tokens"] == qwen.MAX_OUTPUT_TOKENS
    assert captured["timeout"] == 900


def test_call_qwen_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"error":{"message":"bad"}}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            400,
            "Bad Request",
            {},
            ErrorBody(),
        )

    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test")
    monkeypatch.setattr(qwen.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Qwen API error 400"):
        qwen._call_qwen("hello")


def test_agent_records_qwen_trace_and_cost(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "chatcmpl_2",
                "model": "qwen3.7-max",
                "choices": [{"message": {"reasoning_content": "think", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "prompt_tokens_details": {"cached_tokens": 100_000},
                    "completion_tokens_details": {"reasoning_tokens": 250_000},
                },
            },
        )

    monkeypatch.setattr(qwen, "_call_qwen", fake_call)
    monkeypatch.setattr(qwen, "ENABLE_THINKING", "true")
    monkeypatch.setattr(qwen, "THINKING_BUDGET", "omitted")
    monkeypatch.setattr(qwen, "PRESERVE_THINKING", "omitted")
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = qwen.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "qwen"
    assert attempt.usage.reasoning_tokens == 250_000
    assert attempt.usage.output_tokens == 1_000_000
    assert attempt.cost is not None
    assert attempt.cost.output_cost_usd == 5.328
    assert attempt.trace[0]["api_surface"] == "dashscope_openai_chat_completions"
    assert attempt.trace[0]["endpoint"].endswith("/chat/completions")
    assert attempt.trace[0]["enable_thinking"] is True
    assert attempt.trace[0]["thinking_budget"] is None
    assert attempt.trace[0]["model_family"] == "Qwen-Max"
    assert attempt.trace[0]["variant_class"] == "max"
    assert attempt.trace[0]["native_dashscope"] is True
    assert attempt.trace[0]["reasoning_content_chars"] == 5
    assert attempt.trace[0]["gateway"] == "native_dashscope"
    assert calls


def test_variant_class_keeps_model_families_distinct(monkeypatch):
    monkeypatch.setattr(
        qwen,
        "API_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    assert qwen._variant_class("qwen3.7-max") == "max"
    assert qwen._variant_class("qwen3-coder-plus") == "coder"
    assert qwen._variant_class("qwen3.6-35b-a3b") == "open_weight"

    monkeypatch.setattr(qwen, "API_URL", "https://openrouter.ai/api/v1/chat/completions")
    assert qwen._variant_class("qwen3.7-max") == "gateway_or_custom"


def test_pricing_resolves_for_qwen_dashscope_models_only():
    pricing = find_pricing("qwen", "qwen3.7-max")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#qwen3.7-max")
    assert entry["input_usd_per_1m_tokens"] == 1.776
    assert entry["cached_input_usd_per_1m_tokens"] == 1.776
    assert entry["output_usd_per_1m_tokens"] == 5.328

    coder_pricing = find_pricing("qwen", "qwen3-coder-plus")
    assert coder_pricing is not None
    coder_entry, coder_ref = coder_pricing
    assert coder_ref.endswith("#qwen3-coder-plus")
    assert coder_entry["input_usd_per_1m_tokens"] == 0.592
    assert coder_entry["output_usd_per_1m_tokens"] == 2.368

    assert find_pricing("qwen", "qwen3-coder-next") is None
    assert find_pricing("qwen", "openrouter/qwen/qwen3.7-max") is None


def test_reasoning_tokens_are_tracked_inside_qwen_output_bucket():
    payload = {
        "model": "qwen3.7-max",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 1_000_000,
            "completion_tokens_details": {
                "reasoning_tokens": 250_000,
                "text_tokens": 750_000,
            },
            "total_tokens": 1_000_000,
        },
    }
    usage = qwen._usage_from_response(payload)
    assert usage.output_tokens == 1_000_000
    assert usage.reasoning_tokens == 250_000
    cost = estimate_cost(usage)
    assert cost.output_cost_usd == 5.328
    assert cost.total_cost_usd == 5.328


def test_estimate_cost_for_measured_qwen_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="qwen",
            model="qwen3.7-max",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.total_cost_usd is not None
    assert cost.input_cost_usd == 1.5984
    assert cost.cached_input_cost_usd == 0.1776
    assert cost.output_cost_usd == 5.328
    assert cost.total_cost_usd == 7.104
