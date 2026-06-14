"""Manufacturing quote bridge tests."""

from __future__ import annotations

import os

import pytest

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


def _step(tmp_path):
    path = tmp_path / "part.step"
    path.write_text(
        "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n",
        encoding="utf-8",
    )
    return path


def _request(tmp_path):
    return QuoteRequest(
        geometry_path=_step(tmp_path),
        process="cnc_machining",
        material="6061 aluminum",
        quantity=3,
        dfm_passed=True,
        grade_score=4,
    )


def test_vendor_stub_returns_normalized_quote_for_dfm_passing_step(tmp_path):
    request = _request(tmp_path)

    quote = StubQuoteAdapter("protolabs").quote(request)

    assert quote.vendor == "protolabs"
    assert quote.status == "quoted"
    assert quote.price_usd is not None
    assert quote.lead_time_business_days == 7
    assert quote.dfm_flags == ["stub_quote:not_vendor_binding"]
    assert quote.geometry_sha256 == request.geometry_sha256()
    assert quote.approval_required is True
    assert quote.purchase_order_allowed is False


def test_quote_request_rejects_geometry_before_dfm_pass(tmp_path):
    with pytest.raises(ValueError, match="DFM-passing"):
        QuoteRequest(
            geometry_path=_step(tmp_path),
            process="cnc_machining",
            material="6061 aluminum",
            dfm_passed=False,
        )


def test_costing_precheck_runs_before_vendor_quote(tmp_path):
    request = _request(tmp_path)
    seen = []

    def costing_adapter(req):
        seen.append(req.geometry_path.name)
        return CostingEstimate(price_usd=50.0, source="local_costing")

    quote = quote_with_costing_precheck(
        StubQuoteAdapter("sendcutsend"),
        request,
        costing_adapter=costing_adapter,
    )

    assert seen == ["part.step"]
    assert quote.vendor == "sendcutsend"
    assert quote.status == "quoted"
    assert quote.local_estimate_usd == 50.0
    assert quote.local_estimate_source == "local_costing"


def test_failed_costing_precheck_stops_before_vendor(tmp_path):
    request = _request(tmp_path)

    class VendorShouldNotRun(StubQuoteAdapter):
        def quote(self, request, *, local_estimate=None):  # pragma: no cover
            raise AssertionError("vendor quote should not run")

    quote = quote_with_costing_precheck(
        VendorShouldNotRun("xometry"),
        request,
        costing_adapter=lambda _req: {"price_usd": 1000.0, "passed": False},
    )

    assert quote.status == "needs_review"
    assert quote.dfm_flags == ["local_costing_precheck_failed"]
    assert quote.local_estimate_usd == 1000.0


def test_xometry_adapter_normalizes_injected_transport_response(tmp_path, monkeypatch):
    request = _request(tmp_path)
    captured = {}

    def fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "id": "quote-123",
            "price": {"amount": 128.75},
            "leadTimeDays": 5,
            "dfm_flags": [{"code": "min_internal_radius_review"}],
            "manufacturable": True,
            "url": "https://vendor.example/quote-123",
        }

    monkeypatch.setenv("XOMETRY_API_KEY", "secret-token")
    adapter = XometryQuoteAdapter(api_url="https://api.example/quotes", transport=fake_post)

    quote = adapter.quote(request, local_estimate=CostingEstimate(price_usd=120.0))

    assert captured["url"] == "https://api.example/quotes"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["payload"]["safety"] == {
        "mode": "quote_only",
        "purchase_order": False,
        "human_approval_required": True,
    }
    assert captured["payload"]["geometry"]["content_base64"]
    assert quote.vendor == "xometry"
    assert quote.price_usd == 128.75
    assert quote.lead_time_business_days == 5
    assert quote.dfm_flags == ["min_internal_radius_review"]
    assert quote.quote_id == "quote-123"
    assert quote.local_estimate_usd == 120.0


def test_xometry_adapter_reports_unavailable_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("XOMETRY_API_KEY", raising=False)

    quote = XometryQuoteAdapter(
        api_url="https://api.example/quotes",
        transport=lambda _url, _payload, _headers: {},
    ).quote(_request(tmp_path))

    assert quote.status == "unavailable"
    assert quote.dfm_flags == ["api_not_configured"]


def test_quote_bridge_cannot_authorize_purchase_orders(tmp_path):
    quote = StubQuoteAdapter("xometry").quote(_request(tmp_path))

    approval = request_purchase_order_approval(
        quote,
        requested_by="tony",
        reason="Review quoted CNC price before PO.",
    )

    assert approval.human_approval_required is True
    assert approval.may_place_order is False
    with pytest.raises(PurchaseOrderBlocked):
        place_order(quote)
    with pytest.raises(ValueError, match="purchase orders"):
        ManufacturingQuote(vendor="xometry", status="quoted", purchase_order_allowed=True)


def test_quote_module_does_not_require_vendor_credentials_at_import():
    assert "XOMETRY_API_KEY" not in os.environ or isinstance(os.environ["XOMETRY_API_KEY"], str)
