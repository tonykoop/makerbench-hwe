"""Versioned token-pricing helpers for telemetry estimates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .schema import CostReport, UsageReport

PRICING_ROOT = Path(os.environ.get(
    "MAKERBENCH_PRICING_ROOT",
    Path(__file__).resolve().parents[1] / "pricing",
))


def _token_costs(entry: dict[str, Any], usage: UsageReport):
    """Per-bucket USD costs for ``usage`` against a pricing ``entry``.

    Returns ``(input_cost, cached_cost, output_cost, total)`` where any element may
    be ``None`` when the matching token count or price is unavailable. ``total`` is
    ``None`` only when no bucket could be priced at all.
    """
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cached_tokens = usage.cached_input_tokens

    input_price = _number(entry.get("input_usd_per_1m_tokens"))
    output_price = _number(entry.get("output_usd_per_1m_tokens"))
    cached_price = _number(entry.get("cached_input_usd_per_1m_tokens"))

    input_cost = None
    cached_cost = None
    output_cost = None

    if input_tokens is not None and input_price is not None:
        billable_input = input_tokens
        if cached_tokens is not None:
            billable_input = max(input_tokens - cached_tokens, 0)
        input_cost = billable_input * input_price / 1_000_000
    if cached_tokens is not None and cached_price is not None:
        cached_cost = cached_tokens * cached_price / 1_000_000
    if output_tokens is not None and output_price is not None:
        output_cost = output_tokens * output_price / 1_000_000

    parts = [part for part in (input_cost, cached_cost, output_cost) if part is not None]
    total = sum(parts) if parts else None
    return input_cost, cached_cost, output_cost, total


def estimate_cost(usage: UsageReport) -> CostReport:
    """Estimate *actual* USD token cost from measured usage and a versioned pricing file.

    Only authoritative usage (``source == "measured"``) yields an actual cost:
    subscription/local-log rows are billing-opaque and must not be priced here —
    use :func:`estimate_api_equivalent_cost` for their what-if figure instead.
    Unknown usage or missing pricing returns an explicit ``not_available`` report
    instead of guessing. Reasoning tokens are assumed to be included in output
    tokens unless a future pricing file declares separate reasoning pricing.
    """
    if usage.source != "measured" or not usage.model:
        return CostReport(source="not_available")

    pricing = find_pricing(usage.provider, usage.model)
    if pricing is None:
        return CostReport(source="not_available")

    entry, pricing_ref = pricing
    if (
        usage.input_tokens is None
        and usage.output_tokens is None
        and usage.cached_input_tokens is None
    ):
        return CostReport(source="not_available", pricing_ref=pricing_ref)

    input_cost, cached_cost, output_cost, total = _token_costs(entry, usage)
    return CostReport(
        source="estimated" if total is not None else "not_available",
        pricing_ref=pricing_ref,
        input_cost_usd=_round_money(input_cost),
        output_cost_usd=_round_money(output_cost),
        cached_input_cost_usd=_round_money(cached_cost),
        total_cost_usd=_round_money(total),
    )


def estimate_api_equivalent_cost(usage: UsageReport) -> CostReport:
    """What ``usage``'s tokens *would* cost at public API rates — a what-if figure.

    For a subscription/local-log run the actual bill is opaque, so this never sets
    ``total_cost_usd`` (it stays ``None``; cost ``source`` stays ``not_available``).
    It only populates ``api_equivalent_usd`` so the leaderboard can surface an
    estimated public-rate cost *clearly labelled as not money spent*. Returns a
    bare ``not_available`` report (no estimate) when the model/provider is unknown,
    pricing is missing, or no token counts are present.
    """
    if not usage.model:
        return CostReport(source="not_available")

    pricing = find_pricing(usage.provider, usage.model)
    if pricing is None:
        return CostReport(source="not_available")

    entry, pricing_ref = pricing
    if (
        usage.input_tokens is None
        and usage.output_tokens is None
        and usage.cached_input_tokens is None
    ):
        return CostReport(source="not_available", pricing_ref=pricing_ref)

    _input_cost, _cached_cost, _output_cost, total = _token_costs(entry, usage)
    return CostReport(
        source="not_available",
        pricing_ref=pricing_ref,
        api_equivalent_usd=_round_money(total),
    )


def find_pricing(provider: str, model: str) -> tuple[dict[str, Any], str] | None:
    """Find the newest pricing entry for ``provider`` + exact ``model``."""
    if not provider or provider == "unknown":
        return None
    root = PRICING_ROOT
    if not root.exists():
        return None

    for path in sorted(root.glob(f"{provider}-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        models = data.get("models")
        if not isinstance(models, dict) or model not in models:
            continue
        rel = path.relative_to(root.parent).as_posix()
        return models[model], f"{rel}#{model}"
    return None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 12)
