"""Offline unit tests for the direct Mistral API adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "mistral_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("mistral_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mistral = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert mistral._extract_scad(text) == "cube([1,2,3]);"


def test_extract_scad_falls_back_to_raw_text():
    text = "cube([1,2,3]);"
    assert mistral._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_joins_chat_completion_choices():
    payload = {
        "choices": [
            {"message": {"content": "hello"}},
            {"message": {"content": "world"}},
        ]
    }
    assert mistral._extract_text(payload) == "hello\nworld"


def test_extract_text_returns_empty_for_empty_payload():
    assert mistral._extract_text({}) == ""


def test_usage_from_chat_completion_payload():
    payload = {
        "model": "mistral-medium-3.5",
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 500,
            "total_tokens": 700,
        },
    }
    usage = mistral._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "mistral"
    assert usage.model == "mistral-medium-3.5"
    assert usage.input_tokens == 200
    assert usage.output_tokens == 500
    assert usage.total_tokens == 700
    assert usage.cached_input_tokens is None
    assert usage.reasoning_tokens is None


def test_usage_from_response_derives_total_when_absent():
    payload = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 200,
        }
    }
    usage = mistral._usage_from_response(payload)
    assert usage is not None
    assert usage.total_tokens == 300


def test_usage_from_response_returns_none_without_usage():
    assert mistral._usage_from_response({}) is None
    assert mistral._usage_from_response({"usage": "nope"}) is None


def test_call_mistral_requires_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY is not set"):
        mistral._call_mistral("hello")


def test_call_mistral_uses_chat_completion_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl_m1",
                "model": "mistral-medium-3.5",
                "choices": [{"message": {"content": "```scad\ncube(1);\n```"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-test-key")
    monkeypatch.setattr(mistral.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mistral, "API_URL", "https://api.mistral.ai/v1/chat/completions")
    monkeypatch.setattr(mistral, "MODEL", "mistral-medium-3.5")
    monkeypatch.setattr(mistral, "REASONING_EFFORT", "")

    text, raw = mistral._call_mistral("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["id"] == "chatcmpl_m1"
    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer mistral-test-key"
    assert captured["body"]["model"] == "mistral-medium-3.5"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1] == {"role": "user", "content": "build a cube"}
    assert "reasoning_effort" not in captured["body"]
    assert captured["body"]["max_tokens"] == mistral.MAX_OUTPUT_TOKENS
    assert captured["timeout"] == 900


def test_call_mistral_sends_reasoning_effort_when_set(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl_m2",
                "model": "magistral-medium",
                "choices": [{"message": {"content": "```scad\ncube(1);\n```"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-test-key")
    monkeypatch.setattr(mistral.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mistral, "REASONING_EFFORT", "high")

    mistral._call_mistral("build a cube")
    assert captured["body"]["reasoning_effort"] == "high"


def test_call_mistral_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"error":{"message":"unauthorized"}}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError(
            "https://api.mistral.ai/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            ErrorBody(),
        )

    monkeypatch.setenv("MISTRAL_API_KEY", "bad-key")
    monkeypatch.setattr(mistral.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Mistral API error 401"):
        mistral._call_mistral("hello")


def test_agent_records_mistral_trace_and_cost(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "chatcmpl_m3",
                "model": "mistral-medium-3.5",
                "choices": [{"message": {"content": "```scad\ncube([4,5,6]);\n```"}}],
                "usage": {
                    "prompt_tokens": 1_000_000,
                    "completion_tokens": 1_000_000,
                    "total_tokens": 2_000_000,
                },
            },
        )

    monkeypatch.setattr(mistral, "_call_mistral", fake_call)
    monkeypatch.setattr(mistral, "REASONING_EFFORT", "")
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = mistral.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "mistral"
    assert attempt.usage.input_tokens == 1_000_000
    assert attempt.usage.output_tokens == 1_000_000
    assert attempt.cost is not None
    assert attempt.trace[0]["api_surface"] == "mistral_chat_completions"
    assert attempt.trace[0]["provider"] == "mistral"
    assert attempt.trace[0]["gateway"] == "native_mistral"
    assert attempt.trace[0]["model_family"] == "Mistral-Medium"
    assert attempt.trace[0]["reasoning_effort"] == "omitted"
    assert calls


def test_model_family_classification():
    assert mistral._model_family("mistral-medium-3.5") == "Mistral-Medium"
    assert mistral._model_family("mistral-large-3") == "Mistral-Large"
    assert mistral._model_family("magistral-medium") == "Magistral"
    assert mistral._model_family("magistral-small") == "Magistral"
    assert mistral._model_family("devstral-small") == "Devstral"
    assert mistral._model_family("unknown-model") == "Mistral"


def test_pricing_resolves_for_mistral_models():
    pricing = find_pricing("mistral", "mistral-medium-3.5")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#mistral-medium-3.5")
    assert entry["input_usd_per_1m_tokens"] == 0.4
    assert entry["output_usd_per_1m_tokens"] == 2.0

    large_pricing = find_pricing("mistral", "mistral-large-3")
    assert large_pricing is not None
    large_entry, _ = large_pricing
    assert large_entry["input_usd_per_1m_tokens"] == 2.0

    magistral_pricing = find_pricing("mistral", "magistral-medium")
    assert magistral_pricing is not None
    magistral_entry, _ = magistral_pricing
    assert magistral_entry["output_usd_per_1m_tokens"] == 5.0


def test_estimate_cost_for_measured_mistral_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="mistral",
            model="mistral-medium-3.5",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.input_cost_usd == 0.4
    assert cost.output_cost_usd == 2.0
    assert cost.total_cost_usd == 2.4


def test_sum_usage_aggregates_across_iterations():
    reports = [
        UsageReport(source="measured", provider="mistral", model="mistral-medium-3.5",
                    input_tokens=100, output_tokens=200, total_tokens=300),
        UsageReport(source="measured", provider="mistral", model="mistral-medium-3.5",
                    input_tokens=50, output_tokens=100, total_tokens=150),
    ]
    summed = mistral._sum_usage(reports)
    assert summed is not None
    assert summed.input_tokens == 150
    assert summed.output_tokens == 300
    assert summed.total_tokens == 450
    assert summed.provider == "mistral"
