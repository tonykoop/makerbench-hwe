"""Quote-only manufacturing bridge contracts.

This module deliberately stops at a normalized quote plus a human approval
request. It must not place orders, create purchase orders, or move money.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

GeometryFormat = Literal["step", "stp"]
ManufacturingProcess = Literal[
    "cnc_machining",
    "sheet_metal",
    "laser_cutting",
    "3d_printing",
    "injection_molding",
]
QuoteStatus = Literal["quoted", "needs_review", "unavailable", "error"]


class QuoteRequest(BaseModel):
    """Vendor-neutral input for quote-only manufacturing APIs."""

    geometry_path: Path
    geometry_format: GeometryFormat = "step"
    process: ManufacturingProcess
    material: str
    quantity: int = Field(default=1, ge=1)
    units: str = "mm"
    dfm_passed: bool = True
    task_id: str | None = None
    grade_score: int | None = Field(default=None, ge=0, le=4)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("geometry_format", mode="before")
    @classmethod
    def _normalize_format(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower().lstrip(".")
        return value

    @model_validator(mode="after")
    def _validate_ready_for_quote(self):
        if not self.dfm_passed:
            raise ValueError("quote bridge only accepts DFM-passing geometry")
        if not self.geometry_path.is_file():
            raise ValueError(f"geometry_path does not exist: {self.geometry_path}")
        suffix = self.geometry_path.suffix.lower().lstrip(".")
        if suffix not in {"step", "stp"}:
            raise ValueError("quote bridge currently accepts STEP geometry only")
        return self

    def geometry_bytes(self) -> bytes:
        return self.geometry_path.read_bytes()

    def geometry_sha256(self) -> str:
        return hashlib.sha256(self.geometry_bytes()).hexdigest()


class CostingEstimate(BaseModel):
    """Offline costing pre-check result, compatible with a future CostingAdapter."""

    source: str = "costing_adapter"
    price_usd: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    passed: bool = True
    notes: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)


class ManufacturingQuote(BaseModel):
    """Normalized quote response from a live API or deterministic vendor stub."""

    schema_version: str = "0.1"
    vendor: str
    status: QuoteStatus
    price_usd: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    lead_time_business_days: int | None = Field(default=None, ge=0)
    dfm_flags: list[str] = Field(default_factory=list)
    manufacturable: bool | None = None
    quote_id: str | None = None
    vendor_quote_url: str | None = None
    geometry_sha256: str | None = None
    local_estimate_usd: float | None = Field(default=None, ge=0)
    local_estimate_source: str | None = None
    approval_required: bool = True
    purchase_order_allowed: bool = False

    @model_validator(mode="after")
    def _enforce_quote_only_boundary(self):
        if self.purchase_order_allowed:
            raise ValueError("quote bridge responses must not authorize purchase orders")
        if not self.approval_required:
            raise ValueError("quote bridge responses must require human approval")
        return self


class QuoteApprovalRequest(BaseModel):
    """Human-facing approval handoff for a quote."""

    quote: ManufacturingQuote
    requested_by: str
    reason: str = ""
    human_approval_required: bool = True
    may_place_order: bool = False

    @model_validator(mode="after")
    def _enforce_human_gate(self):
        if not self.human_approval_required or self.may_place_order:
            raise ValueError("approval requests cannot bypass the human PO gate")
        return self


class PurchaseOrderBlocked(RuntimeError):
    """Raised when code attempts to place an order through the quote bridge."""


class QuoteAdapter(Protocol):
    vendor: str

    def quote(
        self,
        request: QuoteRequest,
        *,
        local_estimate: CostingEstimate | None = None,
    ) -> ManufacturingQuote:
        """Return a normalized quote without creating a purchase order."""


class CostingAdapter(Protocol):
    def estimate(self, request: QuoteRequest) -> CostingEstimate | dict[str, Any]:
        """Return an offline cost estimate for pre-checking before live quote."""


class StubQuoteAdapter:
    """Deterministic vendor stub used by tests, demos, and unauthenticated runs."""

    _PROCESS_MULTIPLIER = {
        "cnc_machining": 1.8,
        "sheet_metal": 1.2,
        "laser_cutting": 0.9,
        "3d_printing": 0.7,
        "injection_molding": 5.0,
    }

    def __init__(self, vendor: str = "stub", *, setup_usd: float = 45.0) -> None:
        self.vendor = vendor
        self.setup_usd = setup_usd

    def quote(
        self,
        request: QuoteRequest,
        *,
        local_estimate: CostingEstimate | None = None,
    ) -> ManufacturingQuote:
        geometry_size_kb = max(len(request.geometry_bytes()) / 1024, 0.1)
        multiplier = self._PROCESS_MULTIPLIER[request.process]
        price = self.setup_usd + geometry_size_kb * 8.0 * multiplier
        price *= max(request.quantity, 1) ** 0.85
        dfm_flags = ["stub_quote:not_vendor_binding"]
        if local_estimate and local_estimate.price_usd is not None:
            ratio = price / max(local_estimate.price_usd, 0.01)
            if ratio >= 2.0:
                dfm_flags.append("live_quote_exceeds_local_estimate")
        return ManufacturingQuote(
            vendor=self.vendor,
            status="quoted",
            price_usd=round(price, 2),
            lead_time_business_days=7,
            dfm_flags=dfm_flags,
            manufacturable=True,
            quote_id=f"{self.vendor}-{request.geometry_sha256()[:12]}",
            geometry_sha256=request.geometry_sha256(),
            local_estimate_usd=local_estimate.price_usd if local_estimate else None,
            local_estimate_source=local_estimate.source if local_estimate else None,
        )


class ProtolabsQuoteAdapter(StubQuoteAdapter):
    def __init__(self) -> None:
        super().__init__("protolabs")


class SendCutSendQuoteAdapter(StubQuoteAdapter):
    def __init__(self) -> None:
        super().__init__("sendcutsend")


HttpPost = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


class XometryQuoteAdapter:
    """Quote-only Xometry-style API adapter with injected transport.

    The endpoint is configurable because vendor API access is account-specific.
    Tests pass a fake transport; production callers provide an HTTPS transport
    and credentials out of band.
    """

    vendor = "xometry"

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key_env: str = "XOMETRY_API_KEY",
        transport: HttpPost | None = None,
    ) -> None:
        self.api_url = api_url or os.environ.get("XOMETRY_QUOTE_API_URL")
        self.api_key_env = api_key_env
        self.transport = transport

    def quote(
        self,
        request: QuoteRequest,
        *,
        local_estimate: CostingEstimate | None = None,
    ) -> ManufacturingQuote:
        api_key = os.environ.get(self.api_key_env)
        if not self.api_url or not api_key or self.transport is None:
            return ManufacturingQuote(
                vendor=self.vendor,
                status="unavailable",
                dfm_flags=["api_not_configured"],
                geometry_sha256=request.geometry_sha256(),
                local_estimate_usd=local_estimate.price_usd if local_estimate else None,
                local_estimate_source=local_estimate.source if local_estimate else None,
            )

        payload = _quote_payload(request)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        raw = self.transport(self.api_url, payload, headers)
        return _normalize_xometry_response(raw, request, local_estimate)


def quote_with_costing_precheck(
    vendor_adapter: QuoteAdapter,
    request: QuoteRequest,
    *,
    costing_adapter: CostingAdapter | Callable[[QuoteRequest], CostingEstimate | dict[str, Any]]
    | None = None,
) -> ManufacturingQuote:
    """Run the offline costing pre-check before asking a vendor for a quote."""

    estimate = _estimate_cost(request, costing_adapter)
    if estimate and not estimate.passed:
        return ManufacturingQuote(
            vendor=vendor_adapter.vendor,
            status="needs_review",
            dfm_flags=["local_costing_precheck_failed"],
            geometry_sha256=request.geometry_sha256(),
            local_estimate_usd=estimate.price_usd,
            local_estimate_source=estimate.source,
        )
    return vendor_adapter.quote(request, local_estimate=estimate)


def request_purchase_order_approval(
    quote: ManufacturingQuote,
    *,
    requested_by: str,
    reason: str = "",
) -> QuoteApprovalRequest:
    """Create a human approval request; no order is placed."""

    return QuoteApprovalRequest(quote=quote, requested_by=requested_by, reason=reason)


def place_order(*_args: Any, **_kwargs: Any) -> None:
    """Explicitly unsupported: the quote bridge is not a purchasing API."""

    raise PurchaseOrderBlocked(
        "MakerBench quote bridge stops at quote + human approval request; "
        "it cannot place purchase orders."
    )


def _estimate_cost(
    request: QuoteRequest,
    costing_adapter: CostingAdapter | Callable[[QuoteRequest], CostingEstimate | dict[str, Any]]
    | None,
) -> CostingEstimate | None:
    if costing_adapter is None:
        return None
    if callable(costing_adapter) and not hasattr(costing_adapter, "estimate"):
        raw = costing_adapter(request)
    else:
        raw = costing_adapter.estimate(request)  # type: ignore[union-attr]
    if isinstance(raw, CostingEstimate):
        return raw
    return CostingEstimate(**raw)


def _quote_payload(request: QuoteRequest) -> dict[str, Any]:
    content = base64.b64encode(request.geometry_bytes()).decode("ascii")
    return {
        "geometry": {
            "filename": request.geometry_path.name,
            "format": request.geometry_format,
            "sha256": request.geometry_sha256(),
            "content_base64": content,
        },
        "manufacturing": {
            "process": request.process,
            "material": request.material,
            "quantity": request.quantity,
            "units": request.units,
        },
        "safety": {
            "mode": "quote_only",
            "purchase_order": False,
            "human_approval_required": True,
        },
        "metadata": request.metadata,
    }


def _normalize_xometry_response(
    raw: dict[str, Any],
    request: QuoteRequest,
    local_estimate: CostingEstimate | None,
) -> ManufacturingQuote:
    price = _first_number(raw, ("price_usd", "total_usd", "totalPrice"))
    if price is None and isinstance(raw.get("price"), dict):
        price = _first_number(raw["price"], ("amount", "usd"))
    lead_time = _first_int(raw, ("lead_time_business_days", "leadTimeDays", "lead_time_days"))
    flags = _normalize_flags(raw.get("dfm_flags") or raw.get("dfm") or raw.get("warnings"))
    status = raw.get("status")
    if status not in {"quoted", "needs_review", "unavailable", "error"}:
        status = "quoted" if price is not None else "needs_review"
    return ManufacturingQuote(
        vendor="xometry",
        status=status,
        price_usd=price,
        currency=str(raw.get("currency", "USD")),
        lead_time_business_days=lead_time,
        dfm_flags=flags,
        manufacturable=raw.get("manufacturable"),
        quote_id=raw.get("quote_id") or raw.get("id"),
        vendor_quote_url=raw.get("quote_url") or raw.get("url"),
        geometry_sha256=request.geometry_sha256(),
        local_estimate_usd=local_estimate.price_usd if local_estimate else None,
        local_estimate_source=local_estimate.source if local_estimate else None,
    )


def _normalize_flags(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        flags: list[str] = []
        for item in value:
            if isinstance(item, str):
                flags.append(item)
            elif isinstance(item, dict):
                code = item.get("code") or item.get("message") or item.get("detail")
                if code is not None:
                    flags.append(str(code))
        return flags
    if isinstance(value, dict):
        return _normalize_flags(value.get("flags") or value.get("warnings"))
    return [str(value)]


def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None
