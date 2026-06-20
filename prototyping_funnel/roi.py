"""ROI and breakeven analysis for the deflationary prototyping funnel.

Given a development cost (total prototype spend / NRE) and per-unit economics,
this module computes:

* :func:`breakeven_analysis` — units and revenue needed to recoup investment.
* :func:`roi_at_volume` — net-profit ratio at a specified production volume.
* :class:`SensitivityTable` — breakeven sensitivity to unit-price or cost
  changes (useful for "what if I cut unit cost by 10 %?" queries).

All arithmetic is deterministic and dependency-free (pure Python stdlib).

Story coverage: #240 (epic — "deflationary prototyping funnel").
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ROIInput:
    """Inputs for breakeven and ROI analysis.

    ``development_cost_usd`` covers all non-recurring engineering spend:
    prototype iterations, DFM consulting, tooling, and any other pre-production
    investment that does not scale with units shipped.
    """

    development_cost_usd: float
    unit_manufacturing_cost_usd: float
    unit_sale_price_usd: float
    target_volume: int

    def __post_init__(self) -> None:
        if self.development_cost_usd < 0:
            raise ValueError("development_cost_usd must be non-negative")
        if self.unit_manufacturing_cost_usd < 0:
            raise ValueError("unit_manufacturing_cost_usd must be non-negative")
        if self.unit_sale_price_usd <= 0:
            raise ValueError("unit_sale_price_usd must be positive")
        if self.target_volume < 1:
            raise ValueError("target_volume must be >= 1")


@dataclass(frozen=True)
class BreakevenResult:
    """Full breakeven and ROI analysis output.

    Attributes:
        viable: True when the contribution margin is positive (unit_sale_price
            > unit_manufacturing_cost), i.e. the venture can eventually break
            even given enough volume.
        breakeven_units: Exact (possibly fractional) unit count at which
            cumulative contribution margin equals development cost.
        breakeven_units_ceil: ``math.ceil(breakeven_units)`` — the integer
            minimum order quantity that crosses breakeven.
        breakeven_revenue_usd: Revenue at ``breakeven_units_ceil``.
        roi_at_target: Net profit / development cost at ``target_volume``.
            Positive means profitable; negative means still in the red.
        net_profit_at_target_usd: Revenue - variable cost - development cost
            at ``target_volume``.
        gross_margin_fraction: (sale_price - manufacturing_cost) / sale_price.
        payback_iterations_at_qty: If an :class:`IterationRecord` cost series
            is supplied, the number of production runs of size ``quantity``
            needed to recoup development cost.
    """

    development_cost_usd: float
    unit_manufacturing_cost_usd: float
    unit_sale_price_usd: float
    target_volume: int
    viable: bool
    breakeven_units: float
    breakeven_units_ceil: int
    breakeven_revenue_usd: float
    roi_at_target: float
    net_profit_at_target_usd: float
    gross_margin_fraction: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "viable": self.viable,
            "development_cost_usd": self.development_cost_usd,
            "unit_manufacturing_cost_usd": self.unit_manufacturing_cost_usd,
            "unit_sale_price_usd": self.unit_sale_price_usd,
            "target_volume": self.target_volume,
            "gross_margin_fraction": round(self.gross_margin_fraction, 6),
            "breakeven_units": self.breakeven_units,
            "breakeven_units_ceil": self.breakeven_units_ceil,
            "breakeven_revenue_usd": round(self.breakeven_revenue_usd, 2),
            "roi_at_target": round(self.roi_at_target, 6),
            "net_profit_at_target_usd": round(self.net_profit_at_target_usd, 2),
        }


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def breakeven_analysis(roi_input: ROIInput) -> BreakevenResult:
    """Compute breakeven units, revenue at breakeven, and ROI at target volume.

    Formula::

        contribution_margin = unit_sale_price - unit_manufacturing_cost
        breakeven_units     = development_cost / contribution_margin     (if margin > 0)
        roi_at_target       = (revenue - variable_cost - development_cost)
                              / development_cost

    When the contribution margin is <= 0 (unit price ≤ manufacturing cost),
    the project is not viable at any volume and ``breakeven_units`` is
    returned as ``math.inf``.
    """
    margin = roi_input.unit_sale_price_usd - roi_input.unit_manufacturing_cost_usd
    viable = margin > 0

    if not viable:
        breakeven: float = math.inf
        breakeven_ceil = 0
        breakeven_rev = 0.0
    elif roi_input.development_cost_usd == 0:
        # Zero NRE: already profitable at unit 1
        breakeven = 0.0
        breakeven_ceil = 0
        breakeven_rev = 0.0
    else:
        breakeven = roi_input.development_cost_usd / margin
        breakeven_ceil = math.ceil(breakeven)
        breakeven_rev = round(breakeven_ceil * roi_input.unit_sale_price_usd, 6)

    revenue = roi_input.target_volume * roi_input.unit_sale_price_usd
    variable_cost = roi_input.target_volume * roi_input.unit_manufacturing_cost_usd
    net_profit = revenue - variable_cost - roi_input.development_cost_usd
    roi = (
        net_profit / roi_input.development_cost_usd
        if roi_input.development_cost_usd > 0
        else (1.0 if net_profit >= 0 else -1.0)
    )
    gross_margin = margin / roi_input.unit_sale_price_usd

    return BreakevenResult(
        development_cost_usd=roi_input.development_cost_usd,
        unit_manufacturing_cost_usd=roi_input.unit_manufacturing_cost_usd,
        unit_sale_price_usd=roi_input.unit_sale_price_usd,
        target_volume=roi_input.target_volume,
        viable=viable,
        breakeven_units=breakeven,
        breakeven_units_ceil=breakeven_ceil,
        breakeven_revenue_usd=breakeven_rev,
        roi_at_target=roi,
        net_profit_at_target_usd=net_profit,
        gross_margin_fraction=gross_margin,
    )


def roi_at_volume(
    development_cost_usd: float,
    unit_manufacturing_cost_usd: float,
    unit_sale_price_usd: float,
    volume: int,
) -> float:
    """Convenience wrapper: return the ROI ratio at ``volume`` units.

    Returns a float where > 0 means profitable and < 0 means still in the red
    at that volume.
    """
    result = breakeven_analysis(ROIInput(
        development_cost_usd=development_cost_usd,
        unit_manufacturing_cost_usd=unit_manufacturing_cost_usd,
        unit_sale_price_usd=unit_sale_price_usd,
        target_volume=volume,
    ))
    return result.roi_at_target


# ---------------------------------------------------------------------------
# Sensitivity table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SensitivityRow:
    """One row in a breakeven sensitivity sweep."""

    scenario_label: str
    unit_manufacturing_cost_usd: float
    unit_sale_price_usd: float
    breakeven_units: float
    breakeven_units_ceil: int
    roi_at_target: float
    viable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_label": self.scenario_label,
            "unit_manufacturing_cost_usd": self.unit_manufacturing_cost_usd,
            "unit_sale_price_usd": self.unit_sale_price_usd,
            "breakeven_units": self.breakeven_units,
            "breakeven_units_ceil": self.breakeven_units_ceil,
            "roi_at_target": round(self.roi_at_target, 6),
            "viable": self.viable,
        }


class SensitivityTable:
    """Breakeven sensitivity to unit-cost or unit-price perturbations.

    Useful for "what if DFM optimisation cuts unit cost by 20 %?" queries.

    Example::

        table = SensitivityTable(
            base=ROIInput(
                development_cost_usd=5_000,
                unit_manufacturing_cost_usd=40,
                unit_sale_price_usd=99,
                target_volume=500,
            )
        )
        rows = table.sweep_unit_cost(deltas=[-0.30, -0.20, -0.10, 0.0, 0.10])
        for row in rows:
            print(row.scenario_label, row.breakeven_units_ceil, row.roi_at_target)
    """

    def __init__(self, base: ROIInput) -> None:
        self.base = base

    def sweep_unit_cost(self, deltas: list[float]) -> list[SensitivityRow]:
        """Sweep unit manufacturing cost by fractional deltas (e.g. -0.20 = 20% cheaper).

        Positive delta means higher unit cost; negative means cheaper.
        """
        rows: list[SensitivityRow] = []
        for delta in deltas:
            new_cost = self.base.unit_manufacturing_cost_usd * (1.0 + delta)
            new_cost = max(0.0, new_cost)
            label = f"unit_cost{delta:+.0%}"
            result = breakeven_analysis(ROIInput(
                development_cost_usd=self.base.development_cost_usd,
                unit_manufacturing_cost_usd=new_cost,
                unit_sale_price_usd=self.base.unit_sale_price_usd,
                target_volume=self.base.target_volume,
            ))
            rows.append(SensitivityRow(
                scenario_label=label,
                unit_manufacturing_cost_usd=new_cost,
                unit_sale_price_usd=self.base.unit_sale_price_usd,
                breakeven_units=result.breakeven_units,
                breakeven_units_ceil=result.breakeven_units_ceil,
                roi_at_target=result.roi_at_target,
                viable=result.viable,
            ))
        return rows

    def sweep_sale_price(self, deltas: list[float]) -> list[SensitivityRow]:
        """Sweep unit sale price by fractional deltas (e.g. +0.10 = 10% premium).

        Negative delta means lower price; positive means higher.
        """
        rows: list[SensitivityRow] = []
        for delta in deltas:
            new_price = self.base.unit_sale_price_usd * (1.0 + delta)
            if new_price <= 0:
                new_price = 0.01  # clamp to positive
            label = f"sale_price{delta:+.0%}"
            result = breakeven_analysis(ROIInput(
                development_cost_usd=self.base.development_cost_usd,
                unit_manufacturing_cost_usd=self.base.unit_manufacturing_cost_usd,
                unit_sale_price_usd=new_price,
                target_volume=self.base.target_volume,
            ))
            rows.append(SensitivityRow(
                scenario_label=label,
                unit_manufacturing_cost_usd=self.base.unit_manufacturing_cost_usd,
                unit_sale_price_usd=new_price,
                breakeven_units=result.breakeven_units,
                breakeven_units_ceil=result.breakeven_units_ceil,
                roi_at_target=result.roi_at_target,
                viable=result.viable,
            ))
        return rows

    def as_dict(self, deltas: list[float] | None = None) -> dict[str, Any]:
        """Return both cost and price sweeps over ``deltas``."""
        d = deltas or [-0.30, -0.20, -0.10, 0.0, 0.10, 0.20]
        return {
            "base": {
                "development_cost_usd": self.base.development_cost_usd,
                "unit_manufacturing_cost_usd": self.base.unit_manufacturing_cost_usd,
                "unit_sale_price_usd": self.base.unit_sale_price_usd,
                "target_volume": self.base.target_volume,
            },
            "unit_cost_sweep": [r.as_dict() for r in self.sweep_unit_cost(d)],
            "sale_price_sweep": [r.as_dict() for r in self.sweep_sale_price(d)],
        }
