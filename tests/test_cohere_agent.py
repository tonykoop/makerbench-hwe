"""Offline unit tests for the direct Cohere API adapter (no network calls)."""

import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from makerbench.pricing import estimate_cost, find_pricing
from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "cohere_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("cohere_agent_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cohere = _load_agent_module()


def test_extract_scad_from_fenced_block():
    text = "Here you go:\n```scad\ncube([1,2,3]);\n```\nDone."
    assert cohere._extract_scad(text) == "cube([1,2,3]);"


def test_extract_scad_falls_back_to_raw_text():
    text = "cube([1,2,3]);"
    assert cohere._extract_scad(text) == "cube([1,2,3]);"


def test_extract_text_from_v2_message_content_list():
    payload = {
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Hello there!"},
                {"type": "text", "text": "More text."},
            ],
        }
    }
    assert cohere._extract_text(payload) == "Hello there!\nMore text."


def test_extract_text_from_flat_message_content_string():
    payload = {"message": {"role": "assistant", "content": "Direct string content."}}
    assert cohere._extract_text(payload) == "Direct string content."


def test_extract_text_from_flat_text_field():
    payload = {"text": "Flat text field."}
    assert cohere._extract_text(payload) == "Flat text field."


def test_extract_text_from_openai_style_choices():
    payload = {
        "choices": [
            {"message": {"content": "hello"}},
        ]
    }
    assert cohere._extract_text(payload) == "hello"


def test_extract_text_returns_empty_for_empty_payload():
    assert cohere._extract_text({}) == ""


def test_usage_from_billed_units_prefers_billed_over_tokens():
    payload = {
        "model": "command-a-plus-05-2026",
        "usage": {
            "billed_units": {"input_tokens": 100, "output_tokens": 300},
            "tokens": {"input_tokens": 110, "output_tokens": 310},
        },
    }
    usage = cohere._usage_from_response(payload)
    assert usage is not None
    assert usage.source == "measured"
    assert usage.provider == "cohere"
    assert usage.model == "command-a-plus-05-2026"
    assert usage.input_tokens == 100
    assert usage.output_tokens == 300
    assert usage.total_tokens == 400


def test_usage_from_tokens_fallback_when_no_billed_units():
    payload = {
        "model": "command-r",
        "usage": {
            "tokens": {"input_tokens": 50, "output_tokens": 150},
        },
    }
    usage = cohere._usage_from_response(payload)
    assert usage is not None
    assert usage.input_tokens == 50
    assert usage.output_tokens == 150
    assert usage.total_tokens == 200


def test_usage_from_response_returns_none_without_usage():
    assert cohere._usage_from_response({}) is None
    assert cohere._usage_from_response({"usage": "nope"}) is None


def test_usage_from_response_returns_none_when_no_token_counts():
    assert cohere._usage_from_response({"usage": {}}) is None
    assert cohere._usage_from_response({"usage": {"billed_units": {}}}) is None


def test_call_cohere_requires_key(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="COHERE_API_KEY is not set"):
        cohere._call_cohere("hello")


def test_call_cohere_uses_v2_chat_request(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "cohere_id_1",
                "model": "command-a-plus-05-2026",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "```scad\ncube(1);\n```"}],
                },
                "usage": {
                    "billed_units": {"input_tokens": 5, "output_tokens": 10},
                    "tokens": {"input_tokens": 6, "output_tokens": 11},
                },
            }).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("COHERE_API_KEY", "cohere-test-key")
    monkeypatch.setattr(cohere.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cohere, "API_URL", "https://api.cohere.ai/v2/chat")
    monkeypatch.setattr(cohere, "MODEL", "command-a-plus-05-2026")

    text, raw = cohere._call_cohere("build a cube")

    assert text == "```scad\ncube(1);\n```"
    assert raw["id"] == "cohere_id_1"
    assert captured["url"] == "https://api.cohere.ai/v2/chat"
    assert captured["headers"]["Authorization"] == "Bearer cohere-test-key"
    assert captured["body"]["model"] == "command-a-plus-05-2026"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1] == {"role": "user", "content": "build a cube"}
    assert captured["body"]["max_tokens"] == cohere.MAX_OUTPUT_TOKENS
    assert captured["timeout"] == 900


def test_call_cohere_surfaces_http_error(monkeypatch):
    class ErrorBody:
        def read(self):
            return b'{"message":"invalid api key"}'

        def close(self):
            pass

    def fake_urlopen(_req, timeout):
        raise HTTPError(
            "https://api.cohere.ai/v2/chat",
            401,
            "Unauthorized",
            {},
            ErrorBody(),
        )

    monkeypatch.setenv("COHERE_API_KEY", "bad-key")
    monkeypatch.setattr(cohere.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Cohere API error 401"):
        cohere._call_cohere("hello")


def test_agent_records_cohere_trace_and_cost(monkeypatch):
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return (
            "```scad\ncube([4,5,6]);\n```",
            {
                "id": "cohere_id_2",
                "model": "command-a-plus-05-2026",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "```scad\ncube([4,5,6]);\n```"}],
                },
                "usage": {
                    "billed_units": {
                        "input_tokens": 1_000_000,
                        "output_tokens": 1_000_000,
                    },
                },
            },
        )

    monkeypatch.setattr(cohere, "_call_cohere", fake_call)
    spec = TaskSpec(task_id="vented_plate", seed=0, params={}, brief="Build a cube.")

    attempt = cohere.agent(spec, track="blind", tools={})

    assert attempt.source == "cube([4,5,6]);"
    assert attempt.usage is not None
    assert attempt.usage.provider == "cohere"
    assert attempt.usage.input_tokens == 1_000_000
    assert attempt.usage.output_tokens == 1_000_000
    assert attempt.cost is not None
    assert attempt.trace[0]["api_surface"] == "cohere_v2_chat"
    assert attempt.trace[0]["provider"] == "cohere"
    assert attempt.trace[0]["gateway"] == "native_cohere"
    assert attempt.trace[0]["model_family"] == "Command-A"
    assert calls


def test_model_family_classification():
    assert cohere._model_family("command-a-plus-05-2026") == "Command-A"
    assert cohere._model_family("command-r-plus") == "Command-R-Plus"
    assert cohere._model_family("command-r") == "Command-R"
    assert cohere._model_family("command-light") == "Command"
    assert cohere._model_family("unknown-cohere-model") == "Cohere"


def test_pricing_resolves_for_cohere_models():
    pricing = find_pricing("cohere", "command-a-plus-05-2026")
    assert pricing is not None
    entry, ref = pricing
    assert ref.endswith("#command-a-plus-05-2026")
    assert entry["input_usd_per_1m_tokens"] == 2.5
    assert entry["output_usd_per_1m_tokens"] == 10.0

    r_pricing = find_pricing("cohere", "command-r")
    assert r_pricing is not None
    r_entry, _ = r_pricing
    assert r_entry["input_usd_per_1m_tokens"] == 0.15


def test_estimate_cost_for_measured_cohere_usage():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="cohere",
            model="command-a-plus-05-2026",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
    )
    assert cost.source == "estimated"
    assert cost.input_cost_usd == 2.5
    assert cost.output_cost_usd == 10.0
    assert cost.total_cost_usd == 12.5


def test_sum_usage_aggregates_across_iterations():
    reports = [
        UsageReport(source="measured", provider="cohere", model="command-a-plus-05-2026",
                    input_tokens=200, output_tokens=400, total_tokens=600),
        UsageReport(source="measured", provider="cohere", model="command-a-plus-05-2026",
                    input_tokens=100, output_tokens=200, total_tokens=300),
    ]
    summed = cohere._sum_usage(reports)
    assert summed is not None
    assert summed.input_tokens == 300
    assert summed.output_tokens == 600
    assert summed.total_tokens == 900
    assert summed.provider == "cohere"
