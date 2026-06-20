"""Funnel report generator: summarise an entire multi-iteration prototype cycle.

Takes a completed :class:`~.stages.PrototypingFunnel` (or a
:class:`~.runner.FunnelRunResult`) and produces a structured report that can
feed a sprint retrospective, a benchmark submission, or a CAD-leaderboard
run-packet.

Story coverage: #240 (epic — deflationary prototyping funnel).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .cost_model import DeflationaryCurve
from .roi import ROIInput, BreakevenResult, SensitivityTable, breakeven_analysis
from .stages import PrototypingFunnel


# ---------------------------------------------------------------------------
# Report data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeflationaryReport:
    """Structured multi-iteration funnel report.

    Built by :func:`build_report` or :func:`build_report_with_roi`.
    """

    name: str
    iteration_count: int
    cumulative_cost_usd: float
    cumulative_lead_time_days: float
    cost_reduction_fraction: float | None
    lead_time_reduction_fraction: float | None
    validation_rate: float
    curve: list[dict[str, Any]]

    # Optional ROI section (present when unit_sale_price_usd is supplied)
    roi: BreakevenResult | None = None
    sensitivity: dict[str, Any] | None = None

    # Optional deflationary-curve learning stats (present when curve is supplied)
    curve_stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "iteration_count": self.iteration_count,
            "cumulative_cost_usd": round(self.cumulative_cost_usd, 2),
            "cumulative_lead_time_days": round(self.cumulative_lead_time_days, 2),
            "cost_reduction_fraction": (
                round(self.cost_reduction_fraction, 6)
                if self.cost_reduction_fraction is not None
                else None
            ),
            "lead_time_reduction_fraction": (
                round(self.lead_time_reduction_fraction, 6)
                if self.lead_time_reduction_fraction is not None
                else None
            ),
            "validation_rate": round(self.validation_rate, 6),
            "curve": self.curve,
            "roi": self.roi.as_dict() if self.roi else None,
            "sensitivity": self.sensitivity,
            "curve_stats": self.curve_stats,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def text_summary(self) -> str:
        """One-page human-readable summary for sprint retrospectives."""
        lines: list[str] = [
            f"=== Deflationary Funnel Report: {self.name} ===",
            f"Iterations:        {self.iteration_count}",
            f"Cumulative cost:   ${self.cumulative_cost_usd:,.2f}",
            f"Cumulative time:   {self.cumulative_lead_time_days:.1f} days",
        ]
        if self.cost_reduction_fraction is not None:
            lines.append(
                f"Cost reduction:    {self.cost_reduction_fraction:.1%} "
                f"(iteration 1 → latest)"
            )
        if self.lead_time_reduction_fraction is not None:
            lines.append(
                f"Lead-time reduction: {self.lead_time_reduction_fraction:.1%}"
            )
        lines.append(f"Validation rate:   {self.validation_rate:.1%}")

        if self.curve_stats:
            lr = self.curve_stats.get("learning_rate")
            if lr is not None:
                lines.append(f"Learning rate:     {lr:.1%} per doubling")

        if self.roi:
            lines += [
                "",
                "--- ROI Projection ---",
                f"Gross margin:      {self.roi.gross_margin_fraction:.1%}",
                f"Breakeven units:   {self.roi.breakeven_units_ceil}",
                f"Breakeven revenue: ${self.roi.breakeven_revenue_usd:,.2f}",
                f"ROI at target:     {self.roi.roi_at_target:.1%} "
                f"({self.roi.target_volume} units)",
                f"Net profit:        ${self.roi.net_profit_at_target_usd:,.2f}",
                f"Viable:            {'YES' if self.roi.viable else 'NO'}",
            ]

        if self.curve:
            lines += ["", "--- Cost Curve (per iteration) ---"]
            for entry in self.curve:
                lines.append(
                    f"  Iter {entry['iteration']:2d}: "
                    f"${entry['total_cost_usd']:>10,.2f}  "
                    f"({entry['total_lead_time_days']:.1f} days)"
                    + (" ✓ validated" if entry.get("reached_validation") else "")
                )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------


def build_report(
    funnel: PrototypingFunnel,
    *,
    curve: DeflationaryCurve | None = None,
) -> DeflationaryReport:
    """Build a :class:`DeflationaryReport` from a completed funnel.

    Args:
        funnel: A :class:`~.stages.PrototypingFunnel` with at least one
            completed iteration.
        curve: Optional :class:`~.cost_model.DeflationaryCurve` accumulated
            alongside the funnel.  When supplied, learning-rate statistics
            are included in ``curve_stats``.
    """
    summary = funnel.summary()
    curve_stats = curve.as_dict() if curve is not None else None

    return DeflationaryReport(
        name=summary["name"],
        iteration_count=summary["iteration_count"],
        cumulative_cost_usd=summary["cumulative_cost_usd"],
        cumulative_lead_time_days=summary["cumulative_lead_time_days"],
        cost_reduction_fraction=summary["cost_reduction_fraction"],
        lead_time_reduction_fraction=summary["lead_time_reduction_fraction"],
        validation_rate=summary["validation_rate"],
        curve=summary["curve"],
        curve_stats=curve_stats,
    )


def build_report_with_roi(
    funnel: PrototypingFunnel,
    roi_input: ROIInput,
    *,
    curve: DeflationaryCurve | None = None,
    sensitivity_deltas: list[float] | None = None,
) -> DeflationaryReport:
    """Build a full report with ROI projection and optional sensitivity table.

    Args:
        funnel: Completed funnel with recorded iterations.
        roi_input: ROI inputs — the ``development_cost_usd`` should reflect
            the actual cumulative prototype spend (use
            ``funnel.cumulative_cost_usd()``).
        curve: Optional deflationary curve for learning-rate stats.
        sensitivity_deltas: Fractional deltas for the sensitivity sweep
            (e.g. ``[-0.20, -0.10, 0.0, 0.10, 0.20]``).  When omitted a
            default six-point sweep is used.
    """
    report = build_report(funnel, curve=curve)
    roi = breakeven_analysis(roi_input)
    table = SensitivityTable(roi_input)
    sens_dict = table.as_dict(deltas=sensitivity_deltas)

    return DeflationaryReport(
        name=report.name,
        iteration_count=report.iteration_count,
        cumulative_cost_usd=report.cumulative_cost_usd,
        cumulative_lead_time_days=report.cumulative_lead_time_days,
        cost_reduction_fraction=report.cost_reduction_fraction,
        lead_time_reduction_fraction=report.lead_time_reduction_fraction,
        validation_rate=report.validation_rate,
        curve=report.curve,
        roi=roi,
        sensitivity=sens_dict,
        curve_stats=report.curve_stats,
    )
