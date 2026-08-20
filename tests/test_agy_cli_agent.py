"""Offline unit tests for the Antigravity/Gemini CLI adapter (no `agy` process).

Issue #12 pinned the honesty boundary: antigravity keeps no per-run token counts
in its local stores, so nothing could be scraped and rows stayed
`subscription_opaque`. #12's own stated condition for revisiting -- "if a future
antigravity CLI version exposes a usage/JSON output (like `codex exec --json`),
wire it in the same way" -- is met by `agy --output-format json|stream-json`
(verified present in agy v1.1.13).

These tests pin the resulting three-state behavior. The boundary did not move:
a run with usage becomes `local_log` + an API-equivalent what-if cost, and a run
without usage still becomes `subscription_opaque` with null tokens and no cost.
Nothing here fabricates a token count, a dollar figure, or a zero, and no fixture
is a captured real envelope -- every one is hand-written, so no session id or
answer-bearing model output enters this public repo.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

from makerbench.schema import TaskSpec, UsageReport

_AGENT_PATH = Path(__file__).resolve().parents[1] / "agents" / "agy_cli_agent.py"


def _load_agent_module(monkeypatch=None, model="antigravity-gemini-default"):
    if monkeypatch is not None:
        monkeypatch.setenv("MAKERBENCH_MODEL", model)
    spec = importlib.util.spec_from_file_location("agy_cli_under_test", _AGENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agy = _load_agent_module()


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="vented_plate",
        seed=0,
        params={},
        brief="make a vented plate",
        units="mm",
        allowed_tools=[],
    )


# A hand-written `agy --print --output-format json` envelope. The non-token fields
# are present precisely so the leak test below can prove they never escape.
_JSON_ENVELOPE = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 42123,
    "num_turns": 1,
    "session_id": "sess_fixture_0000",
    "result": "```scad\ncube([1,2,3]);\n```",
    "usage": {
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "output_tokens": 200,
        "reasoning_tokens": 50,
    },
})


def _jsonl(*lines: str) -> str:
    return "\n".join(lines)


# A hand-written `--output-format stream-json` stream in the Gemini `usageMetadata`
# vocabulary, with a terminal result envelope carrying the session total.
_STREAM = _jsonl(
    '{"type":"system","subtype":"init","session_id":"sess_fixture_0001"}',
    '{"type":"assistant","message":{"content":[{"type":"text","text":"draft"}]},'
    '"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5}}',
    '{"type":"assistant","message":{"content":[{"type":"text",'
    '"text":"```scad\\ncube([1,2,3]);\\n```"}]}}',
    '{"type":"result","subtype":"success","session_id":"sess_fixture_0001",'
    '"result":"```scad\\ncube([1,2,3]);\\n```",'
    '"usageMetadata":{"promptTokenCount":1000,"cachedContentTokenCount":400,'
    '"candidatesTokenCount":150,"thoughtsTokenCount":50,"totalTokenCount":1200}}',
)


# --------------------------------------------------------------------------
# argv shape
# --------------------------------------------------------------------------

def test_extract_scad_from_fenced_block():
    text = "Here:\n```openscad\ncube([1,2,3]);\n```\nDone."
    assert agy._extract_scad(text) == "cube([1,2,3]);"


def test_base_cmd_places_prompt_after_print_before_timeout(monkeypatch):
    monkeypatch.setattr(agy, "AGY_BIN", "agy-test")
    monkeypatch.setattr(agy, "AGY_ARGS", "--print --some-flag")
    monkeypatch.setattr(agy, "PRINT_TIMEOUT", "20m")

    cmd = agy._base_cmd("build a cube")

    assert cmd[:4] == ["agy-test", "--print", "--some-flag", "build a cube"]
    assert cmd[-2:] == ["--print-timeout", "20m"]


def test_base_cmd_requests_output_format_after_the_prompt(monkeypatch):
    # `agy --print` is a string flag, so the prompt must stay glued to it and every
    # later flag must follow the prompt.
    monkeypatch.setattr(agy, "AGY_BIN", "agy-test")
    monkeypatch.setattr(agy, "AGY_ARGS", "--print")
    monkeypatch.setattr(agy, "OUTPUT_FORMAT", "json")
    monkeypatch.setattr(agy, "PRINT_TIMEOUT", "15m")

    assert agy._base_cmd("build a cube") == [
        "agy-test", "--print", "build a cube",
        "--output-format", "json",
        "--print-timeout", "15m",
    ]


def test_base_cmd_omits_output_format_when_disabled(monkeypatch):
    monkeypatch.setattr(agy, "AGY_BIN", "agy-test")
    monkeypatch.setattr(agy, "AGY_ARGS", "--print")
    monkeypatch.setattr(agy, "OUTPUT_FORMAT", "json")

    assert "--output-format" not in agy._base_cmd("build a cube", "")


def test_base_cmd_respects_output_format_already_in_agy_args(monkeypatch):
    # An explicit override in MAKERBENCH_AGY_ARGS wins; the flag is never doubled.
    monkeypatch.setattr(agy, "AGY_BIN", "agy-test")
    monkeypatch.setattr(agy, "AGY_ARGS", "--output-format stream-json --print")
    monkeypatch.setattr(agy, "OUTPUT_FORMAT", "json")

    cmd = agy._base_cmd("build a cube")

    assert cmd.count("--output-format") == 1
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"


# --------------------------------------------------------------------------
# envelope parsing
# --------------------------------------------------------------------------

def test_parse_json_envelope_pulls_text_and_usage():
    text, usages = agy._parse_print_envelope(_JSON_ENVELOPE)
    assert text == "```scad\ncube([1,2,3]);\n```"
    assert usages == [{
        "input_tokens": 1000, "cached_input_tokens": 400,
        "output_tokens": 200, "reasoning_tokens": 50,
    }]


def test_parse_stream_json_prefers_terminal_result_usage():
    # The per-message block (10/5) must be discarded, not added to the session
    # total, or the estimate would be inflated by double-counting.
    text, usages = agy._parse_print_envelope(_STREAM)
    assert text == "```scad\ncube([1,2,3]);\n```"
    assert len(usages) == 1
    assert usages[0]["promptTokenCount"] == 1000


def test_parse_stream_json_falls_back_to_per_message_usage():
    stream = _jsonl(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"a"}]},'
        '"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"b"}]},'
        '"usageMetadata":{"promptTokenCount":7,"candidatesTokenCount":3}}',
    )
    text, usages = agy._parse_print_envelope(stream)
    assert text == "b"
    assert len(usages) == 2


def test_parse_ignores_non_json_banner_lines():
    stream = _jsonl(
        "Antigravity CLI v1.1.13",
        "not json at all",
        '{"type":"result","result":"final","usage":{"input_tokens":3,"output_tokens":1}}',
    )
    text, usages = agy._parse_print_envelope(stream)
    assert text == "final"
    assert usages == [{"input_tokens": 3, "output_tokens": 1}]


def test_parse_plain_text_output_returns_stdout_and_no_usage():
    # `--output-format text` (or an older CLI) still has to yield usable SCAD.
    raw = "```scad\ncube([1,2,3]);\n```"
    text, usages = agy._parse_print_envelope(raw)
    assert text == raw
    assert usages == []


def test_parse_envelope_without_usage_yields_no_usage():
    envelope = json.dumps({"type": "result", "result": "hi", "session_id": "s"})
    text, usages = agy._parse_print_envelope(envelope)
    assert text == "hi"
    assert usages == []


def test_parse_reads_only_one_usage_block_per_event():
    # Both `usage` and the nested `message.usage` are present; counting both would
    # double the row.
    envelope = json.dumps({
        "type": "result",
        "result": "hi",
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "message": {"usage": {"input_tokens": 10, "output_tokens": 2}},
    })
    _text, usages = agy._parse_print_envelope(envelope)
    assert usages == [{"input_tokens": 10, "output_tokens": 2}]


# --------------------------------------------------------------------------
# token normalization
# --------------------------------------------------------------------------

def test_normalize_cli_vocabulary():
    assert agy._normalize_usage({
        "input_tokens": 1000, "cached_input_tokens": 400,
        "output_tokens": 200, "reasoning_tokens": 50,
    }) == {"input": 1000, "output": 200, "cached": 400, "reasoning": 50}


def test_normalize_gemini_camel_vocabulary_folds_thoughts_into_output():
    # Gemini reports candidate tokens EXCLUDING thinking tokens, so thoughts are
    # folded in to keep the harness contract "reasoning is a subset of output"
    # and to stop thinking tokens from vanishing from the total.
    assert agy._normalize_usage({
        "promptTokenCount": 1000, "cachedContentTokenCount": 400,
        "candidatesTokenCount": 150, "thoughtsTokenCount": 50,
        "totalTokenCount": 1200,
    }) == {"input": 1000, "output": 200, "cached": 400, "reasoning": 50}


def test_normalize_gemini_snake_vocabulary_matches_camel():
    snake = agy._normalize_usage({
        "prompt_token_count": 1000, "cached_content_token_count": 400,
        "candidates_token_count": 150, "thoughts_token_count": 50,
    })
    camel = agy._normalize_usage({
        "promptTokenCount": 1000, "cachedContentTokenCount": 400,
        "candidatesTokenCount": 150, "thoughtsTokenCount": 50,
    })
    assert snake == camel


def test_normalize_does_not_fold_reasoning_into_openai_style_output():
    # In the OpenAI/Anthropic shape reasoning is already inside output_tokens;
    # folding again would over-count.
    assert agy._normalize_usage(
        {"input_tokens": 10, "output_tokens": 200, "reasoning_output_tokens": 50}
    ) == {"input": 10, "output": 200, "cached": 0, "reasoning": 50}


def test_normalize_clamps_cached_to_input():
    # Pricing discounts cached tokens out of input; cached > input would produce a
    # nonsense (negative) billable input.
    assert agy._normalize_usage(
        {"input_tokens": 100, "cached_input_tokens": 500}
    )["cached"] == 100


def test_normalize_rejects_non_integer_and_negative_counts():
    assert agy._normalize_usage({"input_tokens": "1000"}) is None
    assert agy._normalize_usage({"input_tokens": True}) is None
    assert agy._normalize_usage({"input_tokens": -5}) is None
    assert agy._normalize_usage({}) is None
    assert agy._normalize_usage(None) is None


def test_normalize_ignores_unrecognized_keys():
    assert agy._normalize_usage({"session_id": "s", "duration_ms": 5}) is None


# --------------------------------------------------------------------------
# accumulation + usage report
# --------------------------------------------------------------------------

def test_accumulate_sums_across_calls_without_double_counting():
    acc = agy._new_usage_acc()
    agy._accumulate_usage(acc, {"input_tokens": 1000, "cached_input_tokens": 400,
                                "output_tokens": 200, "reasoning_tokens": 50})
    agy._accumulate_usage(acc, {"promptTokenCount": 30, "candidatesTokenCount": 9,
                                "thoughtsTokenCount": 3})
    report = agy._usage_report(acc)
    assert report.source == "local_log"
    assert report.input_tokens == 1030
    assert report.output_tokens == 212
    assert report.cached_input_tokens == 400
    assert report.reasoning_tokens == 53
    # total = input + output (cached ⊆ input, reasoning ⊆ output)
    assert report.total_tokens == 1030 + 212
    assert report.measurement_authority == "local_log"
    assert report.measurement_tool == "agy_cli_json"
    assert report.measurement_source == "antigravity"
    assert report.estimated is True


def test_accumulate_ignores_unusable_block():
    acc = agy._new_usage_acc()
    agy._accumulate_usage(acc, {"session_id": "s"})
    assert acc["any"] is False
    assert agy._usage_report(acc).source == "subscription_opaque"


def test_usage_report_falls_back_to_opaque_without_usage(monkeypatch):
    # An older CLI, or --output-format text, yields no usage block -> honest opaque
    # row with null tokens. This is the boundary #12 pinned and it still holds.
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")

    usage = mod._usage_report(mod._new_usage_acc())

    assert usage.source == "subscription_opaque"
    assert usage.provider == "google"
    assert usage.model == "antigravity-gemini-3.5-flash"
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.cached_input_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.total_tokens is None
    assert usage.measurement_authority is None
    assert usage.measurement_tool is None
    assert usage.measurement_source is None
    assert usage.estimated is False


def test_both_usage_branches_carry_model_and_provider(monkeypatch):
    """Regression guard for the null-`usage.model` bug in pre-#160 agy bundles.

    The 318 committed antigravity rows carry `usage.model: null` because `model=`
    was only added to `_usage_report()` later. A null model defeats pricing lookup
    outright, so both branches must always name the model and provider.
    """
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")

    opaque = mod._usage_report(mod._new_usage_acc())
    acc = mod._new_usage_acc()
    mod._accumulate_usage(acc, {"input_tokens": 10, "output_tokens": 2})
    local = mod._usage_report(acc)

    for report in (opaque, local):
        assert report.model == "antigravity-gemini-3.5-flash"
        assert report.provider == "google"


def test_usage_report_round_trips_through_schema():
    acc = agy._new_usage_acc()
    agy._accumulate_usage(acc, {"input_tokens": 10, "output_tokens": 2})
    report = agy._usage_report(acc)
    assert UsageReport.model_validate(report.model_dump()) == report


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------

def test_cost_report_prices_api_equivalent_for_priced_model(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    acc = mod._new_usage_acc()
    mod._accumulate_usage(acc, {"promptTokenCount": 1000, "cachedContentTokenCount": 400,
                                "candidatesTokenCount": 150, "thoughtsTokenCount": 50})
    usage = mod._usage_report(acc)

    cost = mod._cost_report(usage)

    assert cost is not None
    assert cost.source == "not_available"       # never an actual bill
    assert cost.total_cost_usd is None          # never $0, never a subscription charge
    assert cost.api_equivalent_usd is not None and cost.api_equivalent_usd > 0
    assert cost.pricing_ref.endswith("#antigravity-gemini-3.5-flash")


def test_cost_report_none_for_unpinned_model(monkeypatch):
    # antigravity-gemini-default's underlying model is not pinned, so it is
    # deliberately unpriced: tokens surface, no cost is invented.
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-default")
    acc = mod._new_usage_acc()
    mod._accumulate_usage(acc, {"input_tokens": 1000, "output_tokens": 200})
    usage = mod._usage_report(acc)

    assert usage.source == "local_log"
    assert usage.total_tokens == 1200
    assert mod._cost_report(usage) is None


def test_cost_report_none_for_opaque_usage():
    opaque = agy._usage_report(agy._new_usage_acc())
    assert agy._cost_report(opaque) is None


def test_antigravity_pricing_does_not_shadow_direct_gemini_rows():
    """The new dated file must add aliases only, never re-snapshot gemini-*.

    Direct-API gemini rows keep resolving to the immutable 2026-06-02 snapshot, so
    adding antigravity pricing churns no existing `cost.pricing_ref`.
    """
    from makerbench.pricing import find_pricing

    _entry, ref = find_pricing("google", "gemini-3.5-flash")
    assert ref == "pricing/google-2026-06-02.json#gemini-3.5-flash"
    assert find_pricing("google", "antigravity-gemini-3.1-pro") is None
    assert find_pricing("google", "antigravity-gemini-default") is None


# --------------------------------------------------------------------------
# subprocess behavior: the fallback ladder
# --------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(mod, monkeypatch, responses):
    """Patch subprocess.run to return queued responses; record every argv."""
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    return calls


def test_call_agy_requests_json_and_parses_usage(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    calls = _fake_runner(mod, monkeypatch, [_FakeResult(stdout=_JSON_ENVELOPE)])

    text, usages = mod._call_agy("build a cube")

    assert len(calls) == 1
    assert "--output-format" in calls[0] and "json" in calls[0]
    assert "cube([1,2,3])" in text
    assert usages and usages[0]["input_tokens"] == 1000


def test_unsupported_output_format_flag_retries_once_without_it(monkeypatch):
    """An older `agy` must not be broken by asking for telemetry.

    The flag is dropped, the call re-issued once, the run succeeds, and the row is
    recorded honestly opaque rather than failing the benchmark.
    """
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    calls = _fake_runner(mod, monkeypatch, [
        _FakeResult(returncode=2, stderr="unknown flag: --output-format"),
        _FakeResult(stdout="```scad\ncube(1);\n```"),
    ])

    text, usages = mod._call_agy("build a cube")

    assert len(calls) == 2
    assert "--output-format" in calls[0]
    assert "--output-format" not in calls[1]
    assert "cube(1)" in text
    assert usages == []


def test_unsupported_flag_is_remembered_for_later_calls(monkeypatch):
    # The probe costs one extra invocation per run, not one per call.
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    calls = _fake_runner(mod, monkeypatch, [
        _FakeResult(returncode=2, stderr="unknown flag: --output-format"),
        _FakeResult(stdout="ok"),
    ])

    mod._call_agy("first")
    assert len(calls) == 2
    mod._call_agy("second")
    assert len(calls) == 3
    assert "--output-format" not in calls[2]


def test_unrelated_failure_raises_after_one_retry(monkeypatch):
    # A genuinely broken run must fail loudly, never be logged as zeros or opaque.
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    calls = _fake_runner(mod, monkeypatch, [
        _FakeResult(returncode=1, stderr="quota exhausted"),
    ])

    try:
        mod._call_agy("build a cube")
    except RuntimeError as exc:
        assert "rc=1" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert len(calls) == 2       # original + one retry, no flag-drop probe
    assert all("--output-format" in call for call in calls)


def test_unknown_flag_message_for_a_different_flag_is_not_swallowed(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    calls = _fake_runner(mod, monkeypatch, [
        _FakeResult(returncode=2, stderr="unknown flag: --effort"),
    ])

    try:
        mod._call_agy("build a cube")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")

    assert len(calls) == 2
    assert mod._OUTPUT_FORMAT_SUPPORTED is True


def test_missing_binary_raises_actionable_error(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")

    def boom(_cmd, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(mod.subprocess, "run", boom)

    try:
        mod._call_agy("build a cube")
    except RuntimeError as exc:
        assert "AGY_BIN" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_timeout_raises_actionable_error(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")

    def boom(_cmd, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="agy", timeout=1)

    monkeypatch.setattr(mod.subprocess, "run", boom)

    try:
        mod._call_agy("build a cube")
    except RuntimeError as exc:
        assert "MAKERBENCH_AGY_TIMEOUT" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


# --------------------------------------------------------------------------
# end-to-end agent behavior
# --------------------------------------------------------------------------

def test_agent_flows_local_log_usage_and_api_equivalent_cost(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    monkeypatch.setattr(mod, "_call_agy",
                        lambda prompt, retries=1: mod._parse_print_envelope(_JSON_ENVELOPE))

    attempt = mod.agent(_task(), track="blind", tools={}, perceive=None, budget=1)

    assert attempt.source == "cube([1,2,3]);"
    assert attempt.usage.source == "local_log"
    assert attempt.usage.model == "antigravity-gemini-3.5-flash"
    assert attempt.usage.input_tokens == 1000
    assert attempt.usage.output_tokens == 200
    # Subscription cost is API-equivalent only: legacy cost_usd null, figure in cost.
    assert attempt.cost_usd is None
    assert attempt.cost is not None
    assert attempt.cost.source == "not_available"
    assert attempt.cost.total_cost_usd is None
    assert attempt.cost.api_equivalent_usd is not None and attempt.cost.api_equivalent_usd > 0


def test_agent_keeps_usage_opaque_and_cost_absent_without_envelope(monkeypatch):
    # The #12 boundary, unchanged: no usage source -> opaque row, no fabricated cost.
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-default")
    monkeypatch.setattr(mod, "_call_agy",
                        lambda prompt, retries=1: ("```scad\ncube(1);\n```", []))

    attempt = mod.agent(_task(), track="blind", tools={}, perceive=None, budget=1)

    assert attempt.source == "cube(1);"
    assert attempt.usage is not None
    assert attempt.usage.source == "subscription_opaque"
    assert attempt.usage.provider == "google"
    assert attempt.usage.model == "antigravity-gemini-default"
    assert attempt.usage.total_tokens is None
    assert attempt.cost is None
    assert attempt.cost_usd is None


def test_agent_accumulates_usage_across_perception_iterations(monkeypatch):
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    replies = iter([
        (
            "```scad\ncube(1);\n```",
            [{"input_tokens": 1000, "output_tokens": 200, "cached_input_tokens": 400}],
        ),
        ("```scad\ncube(2);\n```", [{"input_tokens": 300, "output_tokens": 60}]),
        ("LOOKS_GOOD", [{"input_tokens": 40, "output_tokens": 5}]),
    ])
    monkeypatch.setattr(mod, "_call_agy", lambda prompt, retries=1: next(replies))

    attempt = mod.agent(
        _task(), track="perception", tools={},
        perceive=lambda _src: {"compiled": True, "bbox_mm": [1, 1, 1], "warnings": []},
        budget=5,
    )

    assert attempt.iterations == 3
    assert attempt.usage.input_tokens == 1340
    assert attempt.usage.output_tokens == 265
    assert attempt.usage.cached_input_tokens == 400
    assert attempt.usage.total_tokens == 1340 + 265


# --------------------------------------------------------------------------
# privacy contract
# --------------------------------------------------------------------------

def test_envelope_metadata_never_reaches_the_result_bundle(monkeypatch):
    """Only integer token counts may leave the adapter (docs/USAGE_TELEMETRY.md).

    The envelope carries a session id, the full answer text, a duration and a
    home-directory path; none of it may appear in the usage or cost objects that
    get committed to this public repo.
    """
    mod = _load_agent_module(monkeypatch, model="antigravity-gemini-3.5-flash")
    secrets = {
        "session_id": "sess_leakcanary_9f3a",
        "cwd": "/home/leakcanary/projects/secret-benchmark",
        "result": "```scad\n// leakcanary answer body\ncube([1,2,3]);\n```",
    }
    envelope = json.dumps({
        "type": "result",
        "subtype": "success",
        "duration_ms": 91234,
        "session_id": secrets["session_id"],
        "cwd": secrets["cwd"],
        "result": secrets["result"],
        "usage": {"input_tokens": 1000, "output_tokens": 200,
                  "cached_input_tokens": 400, "reasoning_tokens": 50},
    })
    monkeypatch.setattr(mod, "_call_agy",
                        lambda prompt, retries=1: mod._parse_print_envelope(envelope))

    attempt = mod.agent(_task(), track="blind", tools={}, perceive=None, budget=1)

    telemetry = json.dumps(attempt.usage.model_dump()) + json.dumps(attempt.cost.model_dump())
    for secret in ("sess_leakcanary_9f3a", "leakcanary", "/home/", "91234"):
        assert secret not in telemetry
    # And nothing beyond the declared schema fields rides along.
    assert set(attempt.usage.model_dump()) == set(UsageReport.model_fields)
