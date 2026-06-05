"""Opt-in, sanitizing reader for local-log token telemetry (e.g. `ccusage`).

Subscription coding-CLI runs do not expose authoritative API billing, but a local
usage log can still report *token counts*. This is source-agnostic: `ccusage`
spans Claude Code, Codex, Gemini CLI, Qwen, Copilot CLI, and other coding agents,
with source-specific commands (``ccusage claude daily``, ``ccusage codex daily``,
``ccusage gemini daily``, …) as well as an all-sources default. This module turns
a *source-specific* export into a `UsageReport` with ``source="local_log"`` —
useful token numbers that are explicitly **not** authoritative billing — and
records which data source they came from in ``measurement_source``.

Per-row attribution must come from a source-specific export filtered to the
benchmarked harness/channel. Do **not** hand this an all-sources ccusage aggregate
and attach it to one MakerBench row; that would mix unrelated agents' tokens.

The whole module is built around one safety rule: **only integer token counts
leave this code.** It never reads, copies, or returns prompts, file paths, local
usernames, account/organization ids, API keys, timestamps, or any other session
metadata, and it never persists the raw log. A token field is read only if its key
is on an explicit allowlist; everything else in the input is ignored.

Every entry point **fails closed**: on a missing file, malformed JSON, an
unexpected shape, or no usable token counts, it returns a token-free
``subscription_opaque`` report rather than raising — so wiring a local-log reader
into a run can never block or crash the benchmark. See `docs/USAGE_TELEMETRY.md`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .schema import TelemetryProvider, UsageReport

# Allowlist: source key (any casing/underscore variant seen from ccusage-style
# exports) -> canonical UsageReport field. ONLY these keys are ever read; the
# value must be a non-negative integer or it is dropped.
_TOKEN_KEY_ALIASES: dict[str, str] = {
    "inputtokens": "input_tokens",
    "input_tokens": "input_tokens",
    "outputtokens": "output_tokens",
    "output_tokens": "output_tokens",
    "cachereadtokens": "cached_input_tokens",
    "cache_read_tokens": "cached_input_tokens",
    "cachereadinputtokens": "cached_input_tokens",
    "cached_input_tokens": "cached_input_tokens",
    "reasoningtokens": "reasoning_tokens",
    "reasoning_tokens": "reasoning_tokens",
    "totaltokens": "total_tokens",
    "total_tokens": "total_tokens",
}

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def _coerce_token(value: object) -> Optional[int]:
    """Return a non-negative int token count, or None for anything else.

    Bools are rejected (``True`` is not a token count); floats only convert when
    integral; negatives are dropped.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    return None


def _opaque(model: Optional[str], provider: TelemetryProvider) -> UsageReport:
    """The fail-closed result: no tokens, billing explicitly opaque."""
    return UsageReport(source="subscription_opaque", provider=provider, model=model)


def sanitize_local_log_usage(
    *,
    model: Optional[str],
    provider: TelemetryProvider = "anthropic",
    measurement_tool: str = "ccusage",
    measurement_tool_version: Optional[str] = None,
    measurement_source: Optional[str] = None,
    input_tokens: object = None,
    output_tokens: object = None,
    cached_input_tokens: object = None,
    reasoning_tokens: object = None,
    total_tokens: object = None,
) -> UsageReport:
    """Build a sanitized ``local_log`` UsageReport from explicit token counts.

    ``measurement_source`` is the measurement tool's data-source namespace the
    counts were filtered to (e.g. ccusage's ``claude`` | ``codex`` | ``gemini`` |
    ``qwen``); pass the source matching the benchmarked channel so a row is never
    attributed to an all-sources aggregate. Each token argument is coerced to a
    non-negative int or dropped. If no usable token count survives, the result
    fails closed to ``subscription_opaque`` (no counts), so a caller can always
    trust the returned report's ``source``.
    """
    coerced = {
        "input_tokens": _coerce_token(input_tokens),
        "output_tokens": _coerce_token(output_tokens),
        "cached_input_tokens": _coerce_token(cached_input_tokens),
        "reasoning_tokens": _coerce_token(reasoning_tokens),
        "total_tokens": _coerce_token(total_tokens),
    }
    if all(coerced[field] is None for field in _TOKEN_FIELDS):
        return _opaque(model, provider)

    return UsageReport(
        source="local_log",
        provider=provider,
        model=model,
        measurement_authority="local_log",
        measurement_tool=measurement_tool or None,
        measurement_tool_version=measurement_tool_version,
        measurement_source=(measurement_source or None),
        estimated=True,
        **coerced,
    )


def usage_from_ccusage_export(
    data: dict,
    *,
    model: Optional[str],
    source: Optional[str] = None,
    provider: TelemetryProvider = "anthropic",
    measurement_tool_version: Optional[str] = None,
) -> UsageReport:
    """Sanitize an already-parsed ccusage-style usage dict into a UsageReport.

    ``source`` is the ccusage data-source namespace this export was filtered to
    (e.g. ``claude``, ``codex``, ``gemini``, ``qwen``); it is recorded as
    ``measurement_source``. Pass the export from a source-specific command such as
    ``ccusage codex daily`` — not an all-sources aggregate — so the row's tokens
    belong to the benchmarked channel. Reads only keys on ``_TOKEN_KEY_ALIASES``;
    every other field in ``data`` — paths, prompts, session ids, timestamps — is
    ignored and never returned. Fails closed to ``subscription_opaque`` for a
    non-dict input or no usable tokens.
    """
    if not isinstance(data, dict):
        return _opaque(model, provider)

    tokens: dict[str, int] = {}
    for raw_key, raw_value in data.items():
        canonical = _TOKEN_KEY_ALIASES.get(str(raw_key).strip().lower())
        if canonical is None:
            continue
        coerced = _coerce_token(raw_value)
        if coerced is not None:
            tokens[canonical] = coerced

    return sanitize_local_log_usage(
        model=model,
        provider=provider,
        measurement_tool="ccusage",
        measurement_tool_version=measurement_tool_version,
        measurement_source=source,
        **tokens,
    )


def load_ccusage_export(
    path: str,
    *,
    model: Optional[str],
    source: Optional[str] = None,
    provider: TelemetryProvider = "anthropic",
    measurement_tool_version: Optional[str] = None,
) -> UsageReport:
    """Read a single, explicitly-provided ccusage JSON file into a UsageReport.

    Opt-in: the caller passes the exact path and the ``source`` namespace the
    export was filtered to (e.g. ``codex``, ``gemini``). The file is read once,
    parsed, and only its token integers are kept; the raw bytes are never copied
    or persisted. Any error (missing file, bad JSON, wrong shape) fails closed to
    ``subscription_opaque`` so a run is never blocked by telemetry.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _opaque(model, provider)
    if not isinstance(data, dict):
        return _opaque(model, provider)
    return usage_from_ccusage_export(
        data,
        model=model,
        source=source,
        provider=provider,
        measurement_tool_version=measurement_tool_version,
    )
