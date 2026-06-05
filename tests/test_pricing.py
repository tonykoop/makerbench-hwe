"""Telemetry pricing helper tests."""

from makerbench.pricing import estimate_api_equivalent_cost, estimate_cost
from makerbench.schema import UsageReport


def test_estimate_cost_uses_versioned_openai_pricing():
    cost = estimate_cost(
        UsageReport(
            source="measured",
            provider="openai",
            model="gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
        )
    )

    assert cost.source == "estimated"
    assert cost.pricing_ref == "pricing/openai-2026-06-02.json#gpt-5.5"
    assert cost.input_cost_usd == 4.5
    assert cost.cached_input_cost_usd == 0.05
    assert cost.output_cost_usd == 30.0
    assert cost.total_cost_usd == 34.55


def test_estimate_cost_is_not_available_without_measured_usage():
    cost = estimate_cost(
        UsageReport(
            source="subscription_opaque",
            provider="openai",
            model="gpt-5.5",
        )
    )

    assert cost.source == "not_available"
    assert cost.total_cost_usd is None


def test_api_equivalent_cost_prices_local_log_tokens_without_implying_a_bill():
    cost = estimate_api_equivalent_cost(
        UsageReport(
            source="local_log",
            provider="openai",
            model="gpt-5.5",
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=1_000_000,
            estimated=True,
            measurement_authority="local_log",
        )
    )

    # The public-rate what-if matches the measured-usage total above...
    assert cost.api_equivalent_usd == 34.55
    assert cost.pricing_ref == "pricing/openai-2026-06-02.json#gpt-5.5"
    # ...but actual cost stays unstated: this is not a bill.
    assert cost.source == "not_available"
    assert cost.total_cost_usd is None


def test_api_equivalent_cost_is_not_available_without_tokens_or_model():
    assert estimate_api_equivalent_cost(
        UsageReport(source="subscription_opaque", provider="anthropic")
    ).api_equivalent_usd is None
    assert estimate_api_equivalent_cost(
        UsageReport(source="local_log", provider="anthropic", model="claude-haiku-4-5")
    ).api_equivalent_usd is None
