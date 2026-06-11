"""Offline unit tests for the OpenRouter gateway adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.schema import TaskSpec

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "openrouter_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("openrouter_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


openrouter = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert openrouter._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_joins_chat_completion_choices():
    payload = {
        "choices": [
            {"message": {"reasoning": "think", "content": "hello"}},
            {"message": {"content": "world"}},
        ]
    }
    assert openrouter._extract_text(payload) == "hello\nworld"


def test_usage_from_response_maps_openrouter_accounting():
    payload = {
        "model": "deepseek/deepseek-v4-pro",
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 380,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 500,
            "cost": 0.00123,
        },
    }
    usage = openrouter._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "openrouter"
    assert usage.model == "deepseek/deepseek-v4-pro"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 380
    assert usage.cached_input_tokens == 20
    assert usage.reasoning_tokens == 80
    assert usage.total_tokens == 500


def test_usage_from_response_derives_total_when_absent():
    payload = {"usage": {"prompt_tokens": 100, "completion_tokens": 200}}
    usage = openrouter._usage_from_response(payload)
    assert usage is not None
    assert usage.total_tokens == 300


def test_usage_from_response_returns_none_without_metadata():
    assert openrouter._usage_from_response({}) is None
    assert openrouter._usage_from_response({"usage": "nope"}) is None


def test_cost_from_response_reads_gateway_cost():
    assert openrouter._cost_from_response({"usage": {"cost": 0.0042}}) == 0.0042
    assert openrouter._cost_from_response({"usage": {}}) is None
    assert openrouter._cost_from_response({}) is None


def test_sum_costs_builds_cost_report():
    report = openrouter._sum_costs([0.001, 0.002])
    assert report is not None
    assert report.source == "estimated"
    assert report.pricing_ref == "openrouter:response-usage-accounting"
    assert report.total_cost_usd == 0.003
    assert openrouter._sum_costs([]) is None


def test_call_openrouter_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        openrouter._call_openrouter("hello")


def test_reasoning_effort_modes(monkeypatch):
    monkeypatch.setattr(openrouter, "REASONING_EFFORT", "high")
    assert openrouter._reasoning_effort() == "high"
    monkeypatch.setattr(openrouter, "REASONING_EFFORT", "omitted")
    assert openrouter._reasoning_effort() is None
    monkeypatch.setattr(openrouter, "REASONING_EFFORT", "turbo")
    with pytest.raises(RuntimeError, match="MAKERBENCH_REASONING_EFFORT"):
        openrouter._reasoning_effort()


def test_call_openrouter_opts_into_usage_accounting(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "gen_1",
                "model": "deepseek/deepseek-v4-pro",
                "provider": "DeepSeek",
                "choices": [{"message": {"content": "```scad\ncube(1);\n```"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "cost": 0.0001},
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(openrouter, "MODEL", "deepseek/deepseek-v4-pro")
    monkeypatch.setattr(openrouter, "REASONING_EFFORT", "omitted")

    text, raw = openrouter._call_openrouter("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["provider"] == "DeepSeek"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer or-test"
    assert captured["body"]["model"] == "deepseek/deepseek-v4-pro"
    assert captured["body"]["usage"] == {"include": True}
    assert "reasoning" not in captured["body"]
    assert captured["body"]["max_tokens"] == openrouter.MAX_OUTPUT_TOKENS
    assert captured["timeout"] == 900


def test_call_openrouter_sends_reasoning_effort_when_set(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "ok"}}],
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(openrouter, "REASONING_EFFORT", "high")

    openrouter._call_openrouter("hello")
    assert captured["body"]["reasoning"] == {"effort": "high"}


def test_call_openrouter_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"error":{"message":"bad"}}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            402,
            "Payment Required",
            {},
            ErrorBody(),
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="OpenRouter API error 402"):
        openrouter._call_openrouter("hello")


def test_call_openrouter_rejects_embedded_error_payload(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "error": {"code": 502, "message": "upstream provider unavailable"},
            }).encode("utf-8")

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setattr(
        openrouter.urllib.request, "urlopen", lambda req, timeout: FakeResponse()
    )

    with pytest.raises(RuntimeError, match="OpenRouter returned an error payload"):
        openrouter._call_openrouter("hello")


def test_agent_records_gateway_trace_and_response_cost(monkeypatch):
    def fake_call(prompt):
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "gen_2",
                "model": "deepseek/deepseek-v4-pro",
                "provider": "Novita",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 2000,
                    "total_tokens": 3000,
                    "cost": 0.00187,
                },
            },
        )

    monkeypatch.setattr(openrouter, "_call_openrouter", fake_call)
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = openrouter.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "openrouter"
    assert attempt.usage.total_tokens == 3000
    assert attempt.cost is not None
    assert attempt.cost.pricing_ref == "openrouter:response-usage-accounting"
    assert attempt.cost.total_cost_usd == 0.00187
    assert attempt.trace[0]["gateway"] == "openrouter"
    assert attempt.trace[0]["served_by"] == "Novita"
    assert attempt.trace[0]["api_surface"] == "openrouter_chat_completions"
