"""Issue-level acceptance lock for the manufacturing quote bridge (#82)."""

from __future__ import annotations

from pathlib import Path

import pytest

from makerbench.costing import GeometryCostMetrics, ProcessRateProfile
from makerbench.funnel import run_funnel
from makerbench.quote_bridge import (
    CostingEstimate,
    ManufacturingQuote,
    PurchaseOrderBlocked,
    QuoteRequest,
    StubQuoteAdapter,
    XometryQuoteAdapter,
    place_order,
    quote_with_costing_precheck,
    request_purchase_order_approval,
)


ROOT = Path(__file__).resolve().parents[1]
STEP = ROOT / "examples" / "closed_loop_instrument_bridge.step"
DOC = ROOT / "docs" / "QUOTE_BRIDGE.md"


def _request() -> QuoteRequest:
    return QuoteRequest(
        geometry_path=STEP,
        process="cnc_machining",
        material="6061 aluminum",
        quantity=2,
        dfm_passed=True,
        grade_score=4,
        task_id="closed_loop_instrument_bridge",
    )


def test_dfm_passing_step_returns_normalized_stub_quote():
    request = _request()
    quote = StubQuoteAdapter("protolabs").quote(request)

    assert quote.vendor == "protolabs"
    assert quote.status == "quoted"
    assert quote.price_usd is not None and quote.price_usd > 0
    assert quote.lead_time_business_days == 7
    assert quote.dfm_flags == ["stub_quote:not_vendor_binding"]
    assert quote.geometry_sha256 == request.geometry_sha256()
    assert quote.approval_required is True
    assert quote.purchase_order_allowed is False


def test_quote_request_rejects_non_dfm_or_non_step_inputs(tmp_path):
    with pytest.raises(ValueError, match="DFM-passing"):
        QuoteRequest(
            geometry_path=STEP,
            process="cnc_machining",
            material="6061 aluminum",
            dfm_passed=False,
        )

    dxf = tmp_path / "part.dxf"
    dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    with pytest.raises(ValueError, match="STEP geometry only"):
        QuoteRequest(
            geometry_path=dxf,
            process="laser_cutting",
            material="mild steel",
            dfm_passed=True,
        )


def test_costing_precheck_pairs_with_vendor_quote_before_approval():
    seen = []

    def local_costing(request: QuoteRequest) -> CostingEstimate:
        seen.append(request.geometry_path.name)
        return CostingEstimate(
            source="local-costing:acceptance",
            price_usd=21.25,
            passed=True,
            notes="offline pre-check",
        )

    quote = quote_with_costing_precheck(
        StubQuoteAdapter("sendcutsend"),
        _request(),
        costing_adapter=local_costing,
    )

    assert seen == [STEP.name]
    assert quote.vendor == "sendcutsend"
    assert quote.status == "quoted"
    assert quote.local_estimate_usd == 21.25
    assert quote.local_estimate_source == "local-costing:acceptance"
    assert quote.purchase_order_allowed is False


def test_failed_costing_precheck_stops_before_vendor_quote():
    class VendorShouldNotRun(StubQuoteAdapter):
        def quote(self, request, *, local_estimate=None):  # pragma: no cover
            raise AssertionError("vendor adapter must not run after failed local cost")

    quote = quote_with_costing_precheck(
        VendorShouldNotRun("xometry"),
        _request(),
        costing_adapter=lambda _request: {
            "source": "local-costing",
            "price_usd": 999.0,
            "passed": False,
        },
    )

    assert quote.vendor == "xometry"
    assert quote.status == "needs_review"
    assert quote.dfm_flags == ["local_costing_precheck_failed"]
    assert quote.local_estimate_usd == 999.0


def test_xometry_shaped_live_adapter_posts_quote_only_payload(monkeypatch):
    request = _request()
    captured = {}

    def fake_transport(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "id": "quote-acceptance-1",
            "price": {"amount": 128.75},
            "leadTimeDays": 5,
            "dfm_flags": [{"code": "min_internal_radius_review"}],
            "manufacturable": True,
            "url": "https://vendor.example/quote-acceptance-1",
        }

    monkeypatch.setenv("XOMETRY_API_KEY", "secret-token")
    quote = XometryQuoteAdapter(
        api_url="https://api.example/quotes",
        transport=fake_transport,
    ).quote(request, local_estimate=CostingEstimate(price_usd=100.0))

    assert captured["payload"]["safety"] == {
        "mode": "quote_only",
        "purchase_order": False,
        "human_approval_required": True,
    }
    assert captured["payload"]["geometry"]["content_base64"]
    assert quote.quote_id == "quote-acceptance-1"
    assert quote.price_usd == 128.75
    assert quote.dfm_flags == ["min_internal_radius_review"]
    assert quote.local_estimate_usd == 100.0
    assert quote.purchase_order_allowed is False


def test_human_approval_gate_is_terminal_and_blocks_orders():
    quote = StubQuoteAdapter("xometry").quote(_request())
    approval = request_purchase_order_approval(
        quote,
        requested_by="acceptance-test",
        reason="Human must approve any PO outside MakerBench.",
    )

    assert approval.human_approval_required is True
    assert approval.may_place_order is False
    with pytest.raises(PurchaseOrderBlocked):
        place_order(quote)
    with pytest.raises(ValueError, match="purchase orders"):
        ManufacturingQuote(vendor="bad-adapter", status="quoted", purchase_order_allowed=True)


def test_run_funnel_composes_dfm_cost_quote_and_human_approval_offline():
    result = run_funnel(
        STEP,
        quote_process="3d_printing",
        material="PLA",
        cost_metrics=GeometryCostMetrics(
            material_volume_mm3=18_000.0,
            support_material_volume_mm3=1_200.0,
            print_time_minutes=90.0,
        ),
        cost_profile=ProcessRateProfile(
            process_id="additive_3d_print",
            profile_id="fdm-pla-acceptance",
            material_id="pla",
            material_usd_per_cm3=0.05,
            print_usd_per_hour=6.0,
            setup_fee_usd=1.0,
        ),
    )

    assert result.gates_passed == ["dfm", "local_cost", "quote", "approval_request"]
    assert result.blocked_at is None
    assert result.dfm_passed is True
    assert result.quote is not None and result.quote.status == "quoted"
    assert result.approval_request is not None
    assert result.purchase_order_allowed is False
    assert result.quote.local_estimate_usd == result.cost_estimate.total_usd


def test_quote_bridge_docs_pin_credentials_and_no_money_boundary():
    doc = DOC.read_text(encoding="utf-8")

    for phrase in (
        "quote-only",
        "cannot place an order",
        "purchase_order_allowed=false",
        "Credentials stay outside the repo",
        "No step in this loop is allowed to place an order or move money",
    ):
        assert phrase in doc
