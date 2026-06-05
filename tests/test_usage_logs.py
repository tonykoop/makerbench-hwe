"""Local-log token telemetry: sanitization, fail-closed, and no-leak guarantees."""

from __future__ import annotations

import json

from makerbench.usage_logs import (
    load_ccusage_export,
    sanitize_local_log_usage,
    usage_from_ccusage_export,
)


def test_sanitize_builds_local_log_report_with_only_tokens():
    usage = sanitize_local_log_usage(
        model="claude-haiku-4-5",
        input_tokens=1200,
        output_tokens=450,
        cached_input_tokens=100,
        total_tokens=1650,
        measurement_tool_version="1.2.3",
    )

    assert usage.source == "local_log"
    assert usage.measurement_authority == "local_log"
    assert usage.measurement_tool == "ccusage"
    assert usage.measurement_tool_version == "1.2.3"
    assert usage.estimated is True
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 450
    assert usage.total_tokens == 1650


def test_sanitize_fails_closed_to_opaque_without_usable_tokens():
    usage = sanitize_local_log_usage(model="claude-haiku-4-5")

    assert usage.source == "subscription_opaque"
    assert usage.total_tokens is None
    assert usage.estimated is False


def test_sanitize_rejects_non_integer_and_negative_tokens():
    usage = sanitize_local_log_usage(
        model="m",
        input_tokens=-5,           # negative dropped
        output_tokens=True,        # bool dropped
        cached_input_tokens=12.5,  # non-integral float dropped
        total_tokens=900,          # the only survivor
    )

    assert usage.source == "local_log"
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.cached_input_tokens is None
    assert usage.total_tokens == 900


def test_ccusage_export_reads_only_allowlisted_token_keys():
    raw = {
        "inputTokens": 100,
        "outputTokens": 50,
        "cacheReadTokens": 10,
        "totalTokens": 160,
        # Everything below must be ignored and never surface in the report.
        "prompt": "design a vented plate 40x40",
        "cwd": "/home/tony/secret/project",
        "user": "tony",
        "account_id": "acct_12345",
        "apiKey": "sk-do-not-leak",
        "sessionId": "abc-123",
        "timestamp": "2026-06-04T00:00:00Z",
    }
    usage = usage_from_ccusage_export(raw, model="claude-haiku-4-5", source="claude")

    assert usage.source == "local_log"
    assert usage.measurement_source == "claude"
    assert usage.input_tokens == 100
    assert usage.total_tokens == 160

    # No prompt / path / id / key / timestamp may appear anywhere in the report.
    blob = json.dumps(usage.model_dump())
    for leaked in ("design a vented", "/home/tony", "tony", "acct_12345",
                   "sk-do-not-leak", "abc-123", "2026-06-04"):
        assert leaked not in blob


def test_local_log_is_source_agnostic_codex_and_gemini_without_leak():
    """ccusage spans many coding CLIs: a non-Claude (Codex) export is represented
    with its data-source namespace, no leakage, and no actual-cost claim."""
    codex_export = {
        "inputTokens": 300,
        "outputTokens": 120,
        "totalTokens": 420,
        # Non-token fields must never surface.
        "prompt": "write a g-code post",
        "cwd": "/home/dev/codex-session",
        "account": "org_codex_77",
        "apiKey": "sk-codex-secret",
    }
    usage = usage_from_ccusage_export(
        codex_export, model="gpt-5.5", source="codex", provider="openai"
    )

    assert usage.source == "local_log"
    assert usage.measurement_authority == "local_log"
    assert usage.measurement_tool == "ccusage"
    assert usage.measurement_source == "codex"   # not "claude"
    assert usage.provider == "openai"
    assert usage.estimated is True
    assert usage.total_tokens == 420
    blob = json.dumps(usage.model_dump())
    for leaked in ("write a g-code", "/home/dev", "org_codex_77", "sk-codex-secret", "claude"):
        assert leaked not in blob

    # A Gemini row via the explicit sanitizer carries its own source namespace.
    gemini = sanitize_local_log_usage(
        model="gemini-3-pro", provider="google", measurement_source="gemini", total_tokens=999
    )
    assert gemini.source == "local_log"
    assert gemini.measurement_source == "gemini"
    assert gemini.provider == "google"


def test_local_log_source_defaults_to_none_for_backward_compat():
    """Omitting source leaves measurement_source None — legacy callers unaffected."""
    usage = sanitize_local_log_usage(model="claude-haiku-4-5", total_tokens=100)
    assert usage.source == "local_log"
    assert usage.measurement_source is None


def test_ccusage_export_fails_closed_on_garbage():
    assert usage_from_ccusage_export("not a dict", model="m").source == "subscription_opaque"
    assert usage_from_ccusage_export({}, model="m").source == "subscription_opaque"
    assert usage_from_ccusage_export({"foo": "bar"}, model="m").source == "subscription_opaque"


def test_load_export_reads_explicit_path_and_fails_closed(tmp_path):
    good = tmp_path / "ccusage.json"
    good.write_text(json.dumps({"inputTokens": 10, "outputTokens": 5}), encoding="utf-8")
    usage = load_ccusage_export(str(good), model="claude-haiku-4-5")
    assert usage.source == "local_log"
    assert usage.input_tokens == 10

    # Missing file and malformed JSON both fail closed, never raise.
    assert load_ccusage_export(str(tmp_path / "nope.json"), model="m").source == "subscription_opaque"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_ccusage_export(str(bad), model="m").source == "subscription_opaque"


def test_legacy_usage_report_still_parses_without_new_fields():
    """A pre-#102 usage payload (no measurement_* fields) must still validate."""
    from makerbench.schema import UsageReport

    legacy = UsageReport.model_validate({
        "schema_version": "0.1",
        "source": "subscription_opaque",
        "provider": "anthropic",
        "model": "haiku",
    })
    assert legacy.measurement_authority is None
    assert legacy.measurement_tool is None
    assert legacy.estimated is False
