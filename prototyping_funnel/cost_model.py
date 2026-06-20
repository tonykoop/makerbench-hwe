"""Per-process cost estimation with quantity price-breaks and deflationary curve.

This module complements ``makerbench.costing`` (geometry-to-dollars adapters)
with the *unit-economics* layer:

* :class:`QuantityBreak` — a unit-price step at a minimum order quantity (MOQ).
* :class:`PriceSchedule` — an ordered ladder of price-breaks for one
  process + material combination (the vendor's tiered pricing sheet).
* :func:`unit_price_at_qty` — resolve the applicable unit price for a quantity.
* :class:`IterationCostPoint` — cost record for one design-build iteration.
* :class:`DeflationaryCurve` — accumulates iteration points and computes the
  cost-reduction statistics that define the "deflationary" funnel.

Story coverage: #81 (costing interface / CostingAdapter spec), #240 (epic).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Quantity price-breaks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuantityBreak:
    """One step in a vendor's quantity price schedule.

    ``moq`` is the *minimum order quantity* required to qualify for
    ``unit_price_usd``.  A break with ``moq=1`` covers all quantities below
    the next tier's threshold.
    """

    moq: int
    unit_price_usd: float

    def __post_init__(self) -> None:
        if self.moq < 1:
            raise ValueError(f"moq must be >= 1, got {self.moq}")
        if self.unit_price_usd < 0:
            raise ValueError("unit_price_usd must be non-negative")


@dataclass(frozen=True)
class PriceSchedule:
    """Ordered quantity-price ladder for one process + material combination.

    Breaks must be sorted by ascending ``moq``.  The first break must have
    ``moq=1`` so that any quantity has an applicable price.

    Example (3D-print tiered pricing)::

        schedule = PriceSchedule(
            process_id="additive_3d_print",
            material_id="pla-filament",
            breaks=(
                QuantityBreak(moq=1,   unit_price_usd=18.00),
                QuantityBreak(moq=10,  unit_price_usd=12.50),
                QuantityBreak(moq=50,  unit_price_usd=9.00),
                QuantityBreak(moq=200, unit_price_usd=6.75),
            ),
        )
        assert schedule.unit_price_at(1)  == 18.00
        assert schedule.unit_price_at(10) == 12.50
        assert schedule.total_price(50)   == 450.0
    """

    process_id: str
    material_id: str
    breaks: tuple[QuantityBreak, ...]
    currency: str = "USD"
    source: str = "local-estimate"

    def __post_init__(self) -> None:
        if not self.breaks:
            raise ValueError("PriceSchedule requires at least one QuantityBreak")
        moqs = [b.moq for b in self.breaks]
        if moqs != sorted(moqs):
            raise ValueError("QuantityBreaks must be sorted by ascending moq")
        if moqs[0] != 1:
            raise ValueError("first QuantityBreak must have moq=1 (covers qty=1)")
        if len(moqs) != len(set(moqs)):
            raise ValueError("duplicate moq values are not allowed")

    def unit_price_at(self, quantity: int) -> float:
        """Return the unit price that applies for ``quantity``."""
        if quantity < 1:
            raise ValueError(f"quantity must be >= 1, got {quantity}")
        applicable = self.breaks[0]
        for b in self.breaks:
            if quantity >= b.moq:
                applicable = b
        return applicable.unit_price_usd

    def total_price(self, quantity: int) -> float:
        """Total cost for ``quantity`` units at the applicable tier price."""
        return round(self.unit_price_at(quantity) * quantity, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "material_id": self.material_id,
            "currency": self.currency,
            "source": self.source,
            "breaks": [
                {"moq": b.moq, "unit_price_usd": b.unit_price_usd}
                for b in self.breaks
            ],
        }


def unit_price_at_qty(schedule: PriceSchedule, quantity: int) -> float:
    """Convenience alias — resolve the unit price for a quantity from a schedule."""
    return schedule.unit_price_at(quantity)


# ---------------------------------------------------------------------------
# Deflationary curve
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IterationCostPoint:
    """Cost data for one design-build prototype iteration.

    ``unit_price_usd`` is what was paid per part *in this iteration's
    production run*; ``total_cost_usd`` includes the full run (materials,
    vendor fees, setup) plus any iteration-specific NRE such as tooling
    changes or DFM consulting.
    """

    iteration: int
    quantity: int
    unit_price_usd: float
    total_cost_usd: float
    process_id: str
    notes: str = ""

    def __post_init__(self) -> None:
        if self.iteration < 1:
            raise ValueError(f"iteration must be >= 1, got {self.iteration}")
        if self.quantity < 1:
            raise ValueError(f"quantity must be >= 1, got {self.quantity}")
        if self.unit_price_usd < 0:
            raise ValueError("unit_price_usd must be non-negative")
        if self.total_cost_usd < 0:
            raise ValueError("total_cost_usd must be non-negative")


class DeflationaryCurve:
    """Track how unit cost drops across design iterations.

    The "deflationary" label reflects the expected pattern: early prototype
    runs are expensive (low quantity, high scrap, manual steps) while later
    iterations — after DFM optimisation and larger order quantities — approach
    the steady-state production unit cost.

    Usage::

        curve = DeflationaryCurve()
        curve.record(IterationCostPoint(1, quantity=1,  unit_price_usd=220, total_cost_usd=220, process_id="cnc_milling"))
        curve.record(IterationCostPoint(2, quantity=5,  unit_price_usd=80,  total_cost_usd=400, process_id="cnc_milling"))
        curve.record(IterationCostPoint(3, quantity=25, unit_price_usd=38,  total_cost_usd=950, process_id="cnc_milling"))
        print(curve.reduction_fraction())   # (220 - 38) / 220 ≈ 0.827
        print(curve.learning_rate())        # Wright's-law log-log slope
    """

    def __init__(self) -> None:
        self._points: list[IterationCostPoint] = []

    def record(self, point: IterationCostPoint) -> None:
        """Append a cost point; iteration numbers must be sequential from 1."""
        expected = len(self._points) + 1
        if point.iteration != expected:
            raise ValueError(
                f"iterations must be sequential; expected {expected}, "
                f"got {point.iteration}"
            )
        self._points.append(point)

    @property
    def points(self) -> list[IterationCostPoint]:
        return list(self._points)

    def unit_cost_series(self) -> list[float]:
        """Unit price at each recorded iteration (deflationary series)."""
        return [p.unit_price_usd for p in self._points]

    def total_cost_series(self) -> list[float]:
        """Full iteration spend at each recorded point."""
        return [p.total_cost_usd for p in self._points]

    def cumulative_spend_usd(self) -> float:
        """Sum of all iteration total costs — total development spend to date."""
        return round(sum(p.total_cost_usd for p in self._points), 6)

    def reduction_fraction(self) -> float | None:
        """Fraction by which unit price dropped from iteration 1 to the latest.

        A value of 0.70 means unit cost fell 70 % from the first iteration.
        Returns ``None`` when fewer than 2 points are recorded.
        """
        if len(self._points) < 2:
            return None
        first = self._points[0].unit_price_usd
        last = self._points[-1].unit_price_usd
        if first == 0:
            return None
        return round(max(0.0, (first - last) / first), 6)

    def learning_rate(self) -> float | None:
        """Wright's-law learning rate: cost improvement per doubling of iteration count.

        Fits a log-log regression of unit_price vs iteration index.
        Returns ``None`` when fewer than 2 points exist or costs are non-positive.

        A positive return value (e.g. 0.20) means unit cost drops ~20 % each
        time the iteration count doubles — analogous to an 80 % learning curve.
        """
        if len(self._points) < 2:
            return None
        first_cost = self._points[0].unit_price_usd
        last_cost = self._points[-1].unit_price_usd
        n = len(self._points)
        if first_cost <= 0 or last_cost <= 0:
            return None
        ratio = last_cost / first_cost
        if ratio <= 0:
            return None
        # log(ratio) / log(n) gives the power-law exponent b in cost = C0 * n^b
        b = math.log(ratio) / math.log(n)
        # learning rate: improvement per doubling = 1 - 2^b
        lr = 1.0 - (2.0 ** b)
        return round(lr, 6)

    def projected_unit_cost(self, at_iteration: int) -> float | None:
        """Project unit cost at a future iteration using the fitted learning curve.

        Returns ``None`` when fewer than 2 points are recorded.
        """
        if len(self._points) < 2:
            return None
        first_cost = self._points[0].unit_price_usd
        n_now = len(self._points)
        if first_cost <= 0:
            return None
        ratio = self._points[-1].unit_price_usd / first_cost
        if ratio <= 0 or n_now < 2:
            return None
        b = math.log(ratio) / math.log(n_now)
        return round(first_cost * (at_iteration ** b), 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "point_count": len(self._points),
            "cumulative_spend_usd": self.cumulative_spend_usd(),
            "reduction_fraction": self.reduction_fraction(),
            "learning_rate": self.learning_rate(),
            "series": [
                {
                    "iteration": p.iteration,
                    "quantity": p.quantity,
                    "unit_price_usd": p.unit_price_usd,
                    "total_cost_usd": p.total_cost_usd,
                    "process_id": p.process_id,
                    "notes": p.notes,
                }
                for p in self._points
            ],
        }
