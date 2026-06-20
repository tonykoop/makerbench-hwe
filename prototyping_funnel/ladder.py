"""Process-selection ladder for the deflationary prototyping funnel (epic #240).

Compares one or more design variants across all available manufacturing process
profiles at multiple production quantities, producing a ranked cost matrix —
analogous to the existing ``makerbench.sheet_metal_ladder`` primitives but
oriented around the full multi-process costing bridge.

Typical usage::

    from prototyping_funnel.ladder import ProcessLadder, compare_designs
    from prototyping_funnel.mfg_bridge import DesignSpec

    cnc  = DesignSpec("cnc-bracket",  dfm_score=88.0, material_volume_mm3=12_000, ...)
    sls  = DesignSpec("sls-bracket",  dfm_score=79.0, print_time_minutes=90.0, ...)
    designs = [cnc, sls]

    ladder = ProcessLadder(quantities=[1, 10, 50, 200])
    result = ladder.evaluate(designs)

    # Who's cheapest at qty=50?
    best = result.cheapest_design_at_qty(50)
    print(best.design_name, best.cheapest_total_usd)

    # Pretty cost matrix
    print(result.text_table())

Story coverage: #240 (deflationary funnel epic), chunk 4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LadderCell:
    """One cell in the cost matrix: a single design × profile × quantity evaluation.

    Attributes
    ----------
    design_name:
        Name of the :class:`~prototyping_funnel.mfg_bridge.DesignSpec`.
    profile_id:
        Makerbench costing profile identifier (e.g. ``"cnc_alu6061_3axis"``).
    process_id:
        Process family (e.g. ``"cnc_milling"``).
    material_id:
        Material identifier from the profile (e.g. ``"aluminum_6061"``).
    quantity:
        Production quantity this cell was evaluated at.
    unit_cost_usd:
        Per-unit cost as returned by the costing adapter (quantity-independent).
    total_cost_usd:
        ``unit_cost_usd × quantity``.
    rank_at_qty:
        Rank among all profiles evaluated for this design+quantity (1 = cheapest).
    """

    design_name: str
    profile_id: str
    process_id: str
    material_id: str
    quantity: int
    unit_cost_usd: float
    total_cost_usd: float
    rank_at_qty: int  # 1-based: 1 = cheapest

    def as_dict(self) -> dict:
        return {
            "design_name": self.design_name,
            "profile_id": self.profile_id,
            "process_id": self.process_id,
            "material_id": self.material_id,
            "quantity": self.quantity,
            "unit_cost_usd": round(self.unit_cost_usd, 6),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "rank_at_qty": self.rank_at_qty,
        }


@dataclass(frozen=True)
class QuantityRow:
    """All profile evaluations for a single (design, quantity) pair, ranked cheapest-first.

    Attributes
    ----------
    design_name:
        Design the row belongs to.
    quantity:
        Production quantity for this row.
    cells:
        Tuple of :class:`LadderCell` objects, sorted by ``rank_at_qty`` ascending.
    """

    design_name: str
    quantity: int
    cells: tuple[LadderCell, ...]  # sorted cheapest first (rank 1 at index 0)

    @property
    def cheapest_cell(self) -> LadderCell | None:
        """The rank-1 cell, or ``None`` if no profiles matched this design."""
        return self.cells[0] if self.cells else None

    @property
    def cheapest_process_id(self) -> str | None:
        """Process ID of the lowest-cost option, or ``None`` if no cells."""
        c = self.cheapest_cell
        return c.process_id if c else None

    @property
    def cheapest_total_usd(self) -> float:
        """Total cost of the cheapest option, or ``math.inf`` if no cells."""
        c = self.cheapest_cell
        return c.total_cost_usd if c else math.inf

    def as_dict(self) -> dict:
        return {
            "design_name": self.design_name,
            "quantity": self.quantity,
            "cheapest_process_id": self.cheapest_process_id,
            "cheapest_total_usd": (
                None if self.cheapest_total_usd == math.inf else self.cheapest_total_usd
            ),
            "cells": [c.as_dict() for c in self.cells],
        }


@dataclass
class LadderResult:
    """Full ladder evaluation: N designs × M quantities × P profiles.

    Attributes
    ----------
    designs:
        Names of all evaluated designs (in the order passed to :meth:`ProcessLadder.evaluate`).
    quantities:
        Quantity tiers evaluated (ascending).
    rows:
        All :class:`QuantityRow` objects — one per (design, quantity) pair.
    """

    designs: list[str]
    quantities: list[int]
    rows: list[QuantityRow] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def at_quantity(self, qty: int) -> list[QuantityRow]:
        """All rows for a single quantity tier, sorted by design name."""
        return sorted(
            [r for r in self.rows if r.quantity == qty],
            key=lambda r: r.design_name,
        )

    def for_design(self, design_name: str) -> list[QuantityRow]:
        """All rows for one design, sorted ascending by quantity."""
        return sorted(
            [r for r in self.rows if r.design_name == design_name],
            key=lambda r: r.quantity,
        )

    def cheapest_design_at_qty(self, qty: int) -> QuantityRow | None:
        """Return the :class:`QuantityRow` with the lowest cheapest-total cost at *qty*.

        Returns ``None`` if the quantity tier is not in this result.
        """
        rows = self.at_quantity(qty)
        if not rows:
            return None
        return min(rows, key=lambda r: r.cheapest_total_usd)

    def cost_matrix(self) -> dict[str, dict[int, float]]:
        """Return ``{design_name: {qty: cheapest_total_usd}}`` for tabulation."""
        matrix: dict[str, dict[int, float]] = {}
        for row in self.rows:
            matrix.setdefault(row.design_name, {})[row.quantity] = row.cheapest_total_usd
        return matrix

    def as_dict(self) -> dict:
        return {
            "designs": self.designs,
            "quantities": self.quantities,
            "rows": [r.as_dict() for r in self.rows],
        }

    def text_table(self) -> str:
        """Human-readable cost matrix (designs as rows, quantities as columns).

        Example output::

            Design              qty=1    qty=10   qty=50   qty=200
            ----------------------------------------------------------
            cnc-bracket        $15.40  $154.00  $770.00 $3080.00
            printed-housing     $8.22   $82.20  $411.00 $1644.00
        """
        if not self.rows:
            return "(no results)"

        matrix = self.cost_matrix()
        name_w = max(len("Design"), *(len(n) for n in self.designs)) + 2
        col_headers = [f"qty={q}" for q in self.quantities]
        col_w = max(len(h) for h in col_headers) + 4

        header = f"{'Design':<{name_w}}" + "".join(
            f"{h:>{col_w}}" for h in col_headers
        )
        sep = "-" * len(header)
        lines = [header, sep]

        for name in sorted(self.designs):
            row = matrix.get(name, {})
            cells = [
                f"${row[q]:.2f}" if q in row else "N/A"
                for q in self.quantities
            ]
            lines.append(f"{name:<{name_w}}" + "".join(f"{c:>{col_w}}" for c in cells))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ProcessLadder
# ---------------------------------------------------------------------------


class ProcessLadder:
    """Evaluate one or more designs across all manufacturing profiles at multiple quantities.

    Parameters
    ----------
    quantities:
        Production quantity tiers to evaluate. Defaults to ``(1, 10, 50, 100)``.
        Quantities are de-duplicated and sorted ascending.
    profile_ids:
        Restrict bridge evaluation to these profile IDs.
        ``None`` (default) evaluates all presets registered in
        ``makerbench.costing``.
    """

    DEFAULT_QUANTITIES: tuple[int, ...] = (1, 10, 50, 100)

    def __init__(
        self,
        quantities: list[int] | tuple[int, ...] | None = None,
        profile_ids: list[str] | None = None,
    ) -> None:
        raw = list(quantities) if quantities is not None else list(self.DEFAULT_QUANTITIES)
        if not raw:
            raise ValueError("quantities must not be empty")
        for q in raw:
            if not isinstance(q, int) or q < 1:
                raise ValueError(
                    f"each quantity must be a positive integer, got {q!r}"
                )
        # de-dup + sort
        self._quantities: tuple[int, ...] = tuple(sorted(set(raw)))
        self._profile_ids = profile_ids

    @property
    def quantities(self) -> tuple[int, ...]:
        return self._quantities

    def evaluate(self, designs: list) -> LadderResult:
        """Evaluate all designs at all configured quantities.

        Parameters
        ----------
        designs:
            Sequence of :class:`~prototyping_funnel.mfg_bridge.DesignSpec`.

        Returns
        -------
        LadderResult
            Populated cost matrix; silently omits profiles that are incompatible
            with a design's geometry (same behaviour as
            :class:`~prototyping_funnel.mfg_bridge.ManufacturingBridge`).
        """
        from .mfg_bridge import ManufacturingBridge

        if not designs:
            return LadderResult(
                designs=[],
                quantities=list(self._quantities),
                rows=[],
            )

        bridge = ManufacturingBridge(profile_ids=self._profile_ids)
        all_rows: list[QuantityRow] = []
        design_names: list[str] = []

        for design in designs:
            design_names.append(design.name)
            # One bridge call per design; unit costs are quantity-independent
            bridge_result = bridge.evaluate(design)
            base_results = bridge_result.results  # already sorted cheapest-first

            for qty in self._quantities:
                # Re-rank by total cost at this quantity (same ordering as unit cost
                # since total = unit × qty, but explicit for correctness & clarity)
                scaled: list[tuple[float, object]] = [
                    (pr.total_usd * qty, pr) for pr in base_results
                ]
                scaled.sort(key=lambda t: t[0])

                ladder_cells = tuple(
                    LadderCell(
                        design_name=design.name,
                        profile_id=pr.profile_id,
                        process_id=pr.process_id,
                        material_id=pr.material_id,
                        quantity=qty,
                        unit_cost_usd=pr.total_usd,
                        total_cost_usd=total_usd,
                        rank_at_qty=rank + 1,
                    )
                    for rank, (total_usd, pr) in enumerate(scaled)
                )

                all_rows.append(
                    QuantityRow(
                        design_name=design.name,
                        quantity=qty,
                        cells=ladder_cells,
                    )
                )

        return LadderResult(
            designs=design_names,
            quantities=list(self._quantities),
            rows=all_rows,
        )


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------


def compare_designs(
    designs: list,
    quantities: list[int] | None = None,
    profile_ids: list[str] | None = None,
) -> LadderResult:
    """Compare multiple :class:`~prototyping_funnel.mfg_bridge.DesignSpec` objects.

    Convenience wrapper around :class:`ProcessLadder`.

    Parameters
    ----------
    designs:
        Design specifications to compare.
    quantities:
        Production quantity tiers. Defaults to ``[1, 10, 50, 100]``.
    profile_ids:
        Restrict bridge evaluation to these profile IDs.

    Returns
    -------
    LadderResult
    """
    return ProcessLadder(quantities=quantities, profile_ids=profile_ids).evaluate(designs)
