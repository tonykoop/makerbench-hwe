"""Deflationary prototyping funnel: geometry -> DFM -> local cost -> quote.

This module composes the three existing offline-safe layers into the single
closed loop described in epic #240, without adding any network dependency:

* **Gate 1 — deterministic DFM** via :func:`makerbench_core.score_file`
  (no LLM judge, no CAD kernel, no oracle access).
* **Gate 2 — local manufacturing-cost estimate** via the deterministic
  :mod:`makerbench.costing` adapters (optional; runs when metrics + profile are
  supplied).
* **Gate 3 — vendor quote** via a :mod:`makerbench.quote_bridge` adapter. The
  default adapter is the deterministic ``StubQuoteAdapter`` so the funnel — and
  CI — never contacts a real vendor.
* **Gate 4 — human approval request**. The funnel stops here: it builds a
  ``QuoteApprovalRequest`` and never places an order or moves money.

The whole funnel is deterministic and offline by default, so the same inputs
always produce the same result (the "re-quote" step of an optimize loop is just
calling :func:`run_funnel` again with an adjusted feature tree).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from makerbench_core import score_file

from .costing import (
    AdditivePrintCostingAdapter,
    CncCostingAdapter,
    GeometryCostMetrics,
    ManufacturingCostEstimate,
    ProcessRateProfile,
    SheetLaserCostingAdapter,
)
from .quote_bridge import (
    CostingEstimate,
    ManufacturingQuote,
    QuoteAdapter,
    QuoteApprovalRequest,
    QuoteRequest,
    StubQuoteAdapter,
    request_purchase_order_approval,
)
from .quote_bridge import ManufacturingProcess as QuoteProcess

# Local wiring only (deliberately not importing a dispatcher, so this module is
# independent of any single costing PR): map a costing process to its adapter.
_COST_ADAPTERS = {
    "cnc_milling": CncCostingAdapter,
    "sheet_laser": SheetLaserCostingAdapter,
    "additive_3d_print": AdditivePrintCostingAdapter,
}

_STEP_SUFFIXES = {"step", "stp"}


@dataclass(frozen=True)
class FunnelResult:
    """Structured result of one pass through the deflationary funnel."""

    geometry_path: str
    gates_passed: list[str]
    blocked_at: str | None
    dfm_score: float
    dfm_passed: bool
    dfm: dict[str, Any]
    cost_estimate: ManufacturingCostEstimate | None = None
    quote: ManufacturingQuote | None = None
    approval_request: QuoteApprovalRequest | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def purchase_order_allowed(self) -> bool:
        """The funnel never authorizes a purchase order (safety invariant)."""
        if self.quote is not None and self.quote.purchase_order_allowed:
            return True
        if self.approval_request is not None and self.approval_request.may_place_order:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry_path": self.geometry_path,
            "gates_passed": list(self.gates_passed),
            "blocked_at": self.blocked_at,
            "dfm_score": self.dfm_score,
            "dfm_passed": self.dfm_passed,
            "dfm": self.dfm,
            "cost_estimate": self.cost_estimate.as_dict() if self.cost_estimate else None,
            "quote": self.quote.model_dump() if self.quote else None,
            "approval_request": (
                self.approval_request.model_dump() if self.approval_request else None
            ),
            "purchase_order_allowed": self.purchase_order_allowed,
            "notes": list(self.notes),
        }


def run_funnel(
    geometry_path: str | Path,
    *,
    quote_process: QuoteProcess,
    material: str,
    quantity: int = 1,
    cost_metrics: GeometryCostMetrics | None = None,
    cost_profile: ProcessRateProfile | None = None,
    dfm_fail_under: float = 80.0,
    quote_adapter: QuoteAdapter | None = None,
    requested_by: str = "makerbench-funnel",
) -> FunnelResult:
    """Run a part through the funnel, stopping at the human approval request.

    Returns a :class:`FunnelResult` describing which gates passed and where (if
    anywhere) the part was blocked. No order is ever placed.
    """

    path = Path(geometry_path)
    gates: list[str] = []
    notes: list[str] = []

    # --- Gate 1: deterministic DFM -------------------------------------------
    score = score_file(path)
    dfm = score.to_dict()
    dfm_passed = score.passed and score.makerbench_dfm_score >= dfm_fail_under
    if not dfm_passed:
        notes.append(
            f"DFM gate failed: score {score.makerbench_dfm_score:.1f}% "
            f"(threshold {dfm_fail_under:.1f}%, passed={score.passed})"
        )
        return FunnelResult(
            geometry_path=str(path),
            gates_passed=gates,
            blocked_at="dfm",
            dfm_score=score.makerbench_dfm_score,
            dfm_passed=False,
            dfm=dfm,
            notes=notes,
        )
    gates.append("dfm")

    # --- Gate 2: optional local cost estimate --------------------------------
    cost_estimate: ManufacturingCostEstimate | None = None
    costing_precheck: CostingEstimate | None = None
    if cost_metrics is not None and cost_profile is not None:
        adapter_cls = _COST_ADAPTERS.get(cost_profile.process_id)
        if adapter_cls is None:  # pragma: no cover - guarded by the Literal type
            raise ValueError(f"no costing adapter for {cost_profile.process_id!r}")
        cost_estimate = adapter_cls().estimate(cost_metrics, cost_profile)
        costing_precheck = CostingEstimate(
            source=f"costing:{cost_profile.profile_id}",
            price_usd=cost_estimate.total_usd,
            currency=cost_estimate.currency,
            passed=True,
            notes="local deterministic estimate; not a vendor quote",
        )
        gates.append("local_cost")
    else:
        notes.append("local cost gate skipped: no cost_metrics/cost_profile supplied")

    # --- Gate 3: vendor quote (deterministic stub by default) ----------------
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in _STEP_SUFFIXES:
        notes.append(
            f"quote gate skipped: bridge accepts STEP only, got '.{suffix or '<none>'}'"
        )
        return FunnelResult(
            geometry_path=str(path),
            gates_passed=gates,
            blocked_at="quote",
            dfm_score=score.makerbench_dfm_score,
            dfm_passed=True,
            dfm=dfm,
            cost_estimate=cost_estimate,
            notes=notes,
        )

    request = QuoteRequest(
        geometry_path=path,
        geometry_format=suffix,  # type: ignore[arg-type]
        process=quote_process,
        material=material,
        quantity=quantity,
        dfm_passed=True,
    )
    adapter = quote_adapter or StubQuoteAdapter()
    quote = adapter.quote(request, local_estimate=costing_precheck)
    gates.append("quote")

    # --- Gate 4: human approval request (terminal; no order placed) ----------
    approval = request_purchase_order_approval(
        quote,
        requested_by=requested_by,
        reason="deflationary funnel reached the quote stage; human approval required before any PO",
    )
    gates.append("approval_request")

    return FunnelResult(
        geometry_path=str(path),
        gates_passed=gates,
        blocked_at=None,
        dfm_score=score.makerbench_dfm_score,
        dfm_passed=True,
        dfm=dfm,
        cost_estimate=cost_estimate,
        quote=quote,
        approval_request=approval,
        notes=notes,
    )
