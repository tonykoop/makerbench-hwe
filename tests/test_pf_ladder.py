"""Tests for prototyping_funnel.ladder — process-selection ladder (epic #240).

Covers:
- LadderCell construction and as_dict()
- QuantityRow properties (cheapest, inf when empty)
- ProcessLadder construction validation
- ProcessLadder.evaluate() with single / multiple designs and quantities
- LadderResult query helpers: at_quantity, for_design, cheapest_design_at_qty, cost_matrix
- JSON serialisability of LadderResult
- text_table() formatting
- compare_designs() convenience wrapper
- Edge cases: empty designs, single cell, duplicate quantities, missing quantity
"""

from __future__ import annotations

import json
import math

import pytest

from prototyping_funnel.ladder import (
    LadderCell,
    LadderResult,
    ProcessLadder,
    QuantityRow,
    compare_designs,
)
from prototyping_funnel.mfg_bridge import DesignSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cnc_design():
    return DesignSpec(
        name="cnc-bracket",
        dfm_score=88.0,
        material_volume_mm3=12_000,
        removed_volume_mm3=3_000,
        hole_count=6,
        setup_count=1,
        tool_change_count=2,
    )


@pytest.fixture()
def additive_design():
    return DesignSpec(
        name="printed-housing",
        dfm_score=75.0,
        material_volume_mm3=8_000,
        print_time_minutes=120.0,
        support_material_volume_mm3=500.0,
    )


@pytest.fixture()
def sheet_design():
    return DesignSpec(
        name="sheet-chassis",
        dfm_score=82.0,
        sheet_area_mm2=25_000.0,
        cut_length_mm=800.0,
        bend_count=3,
    )


# ---------------------------------------------------------------------------
# LadderCell
# ---------------------------------------------------------------------------


class TestLadderCell:
    def _cell(self, rank=1, total=100.0, qty=10):
        return LadderCell(
            design_name="my-part",
            profile_id="cnc_alu_3axis",
            process_id="cnc_milling",
            material_id="aluminum_6061",
            quantity=qty,
            unit_cost_usd=total / qty,
            total_cost_usd=total,
            rank_at_qty=rank,
        )

    def test_as_dict_has_all_keys(self):
        d = self._cell().as_dict()
        expected = {
            "design_name", "profile_id", "process_id", "material_id",
            "quantity", "unit_cost_usd", "total_cost_usd", "rank_at_qty",
        }
        assert set(d.keys()) == expected

    def test_as_dict_values_correct(self):
        d = self._cell(rank=3, total=200.0, qty=10).as_dict()
        assert d["rank_at_qty"] == 3
        assert d["total_cost_usd"] == pytest.approx(200.0)
        assert d["unit_cost_usd"] == pytest.approx(20.0)
        assert d["quantity"] == 10

    def test_as_dict_is_json_serializable(self):
        json.dumps(self._cell().as_dict())

    def test_is_frozen_raises_on_mutation(self):
        cell = self._cell()
        with pytest.raises((AttributeError, TypeError)):
            cell.rank_at_qty = 99  # type: ignore[misc]

    def test_process_id_stored(self):
        cell = self._cell()
        assert cell.process_id == "cnc_milling"

    def test_design_name_stored(self):
        cell = self._cell()
        assert cell.design_name == "my-part"


# ---------------------------------------------------------------------------
# QuantityRow
# ---------------------------------------------------------------------------


class TestQuantityRow:
    def _cell(self, rank, total):
        return LadderCell(
            design_name="d",
            profile_id=f"p{rank}",
            process_id="proc",
            material_id="mat",
            quantity=10,
            unit_cost_usd=total / 10,
            total_cost_usd=total,
            rank_at_qty=rank,
        )

    def test_cheapest_cell_is_rank_1(self):
        c1 = self._cell(1, 10.0)
        c2 = self._cell(2, 20.0)
        row = QuantityRow("d", 10, (c1, c2))
        assert row.cheapest_cell is c1

    def test_cheapest_process_id(self):
        c = self._cell(1, 10.0)
        row = QuantityRow("d", 10, (c,))
        assert row.cheapest_process_id == "proc"

    def test_cheapest_total_usd(self):
        c = self._cell(1, 42.5)
        row = QuantityRow("d", 10, (c,))
        assert row.cheapest_total_usd == pytest.approx(42.5)

    def test_empty_cells_cheapest_cell_is_none(self):
        row = QuantityRow("d", 1, ())
        assert row.cheapest_cell is None

    def test_empty_cells_cheapest_process_id_is_none(self):
        row = QuantityRow("d", 1, ())
        assert row.cheapest_process_id is None

    def test_empty_cells_cheapest_total_usd_is_inf(self):
        row = QuantityRow("d", 1, ())
        assert row.cheapest_total_usd == math.inf

    def test_as_dict_structure(self):
        c = self._cell(1, 50.0)
        row = QuantityRow("d", 10, (c,))
        d = row.as_dict()
        assert d["design_name"] == "d"
        assert d["quantity"] == 10
        assert "cheapest_process_id" in d
        assert "cells" in d
        assert isinstance(d["cells"], list)

    def test_as_dict_none_for_empty_inf(self):
        # Empty row → cheapest_total_usd = inf → serialised as None
        row = QuantityRow("d", 1, ())
        d = row.as_dict()
        assert d["cheapest_total_usd"] is None

    def test_as_dict_is_json_serializable(self):
        c = self._cell(1, 100.0)
        row = QuantityRow("d", 10, (c,))
        json.dumps(row.as_dict())


# ---------------------------------------------------------------------------
# ProcessLadder — construction
# ---------------------------------------------------------------------------


class TestProcessLadderConstruction:
    def test_default_quantities(self):
        ladder = ProcessLadder()
        assert ladder.quantities == (1, 10, 50, 100)

    def test_custom_quantities_sorted(self):
        ladder = ProcessLadder(quantities=[100, 1, 10])
        assert ladder.quantities == (1, 10, 100)

    def test_duplicate_quantities_deduped(self):
        ladder = ProcessLadder(quantities=[10, 10, 50])
        assert ladder.quantities == (10, 50)

    def test_empty_quantities_raises(self):
        with pytest.raises(ValueError, match="empty"):
            ProcessLadder(quantities=[])

    def test_zero_quantity_raises(self):
        with pytest.raises(ValueError):
            ProcessLadder(quantities=[0, 10])

    def test_negative_quantity_raises(self):
        with pytest.raises(ValueError):
            ProcessLadder(quantities=[-5])

    def test_float_quantity_raises(self):
        with pytest.raises((ValueError, TypeError)):
            ProcessLadder(quantities=[1.5])  # type: ignore[list-item]

    def test_single_quantity_ok(self):
        ladder = ProcessLadder(quantities=[1])
        assert ladder.quantities == (1,)


# ---------------------------------------------------------------------------
# ProcessLadder — evaluate
# ---------------------------------------------------------------------------


class TestProcessLadderEvaluate:
    def test_empty_designs_returns_empty_result(self):
        ladder = ProcessLadder()
        result = ladder.evaluate([])
        assert result.designs == []
        assert result.rows == []
        assert result.quantities == [1, 10, 50, 100]

    def test_single_design_single_qty(self, cnc_design):
        ladder = ProcessLadder(quantities=[10])
        result = ladder.evaluate([cnc_design])
        assert result.designs == ["cnc-bracket"]
        assert result.quantities == [10]
        # At least one profile should match a CNC-capable design
        assert len(result.rows) >= 1
        for row in result.rows:
            assert len(row.cells) >= 1

    def test_rows_count_equals_designs_times_qtys(self, cnc_design, additive_design):
        ladder = ProcessLadder(quantities=[1, 100])
        result = ladder.evaluate([cnc_design, additive_design])
        # 2 designs × 2 quantities = 4 QuantityRow objects
        assert len(result.rows) == 4

    def test_rows_have_correct_quantities(self, cnc_design):
        ladder = ProcessLadder(quantities=[5, 25, 75])
        result = ladder.evaluate([cnc_design])
        qtys = {r.quantity for r in result.rows}
        assert qtys == {5, 25, 75}

    def test_row_design_names_match_inputs(self, cnc_design, additive_design):
        ladder = ProcessLadder(quantities=[10])
        result = ladder.evaluate([cnc_design, additive_design])
        names = {r.design_name for r in result.rows}
        assert names == {"cnc-bracket", "printed-housing"}

    def test_cells_are_sorted_cheapest_first(self, cnc_design):
        ladder = ProcessLadder(quantities=[50])
        result = ladder.evaluate([cnc_design])
        for row in result.rows:
            totals = [c.total_cost_usd for c in row.cells]
            assert totals == sorted(totals), "cells not sorted cheapest-first"

    def test_cells_ranks_are_sequential(self, cnc_design):
        ladder = ProcessLadder(quantities=[10])
        result = ladder.evaluate([cnc_design])
        for row in result.rows:
            ranks = [c.rank_at_qty for c in row.cells]
            assert ranks == list(range(1, len(ranks) + 1))

    def test_total_usd_is_unit_times_qty(self, cnc_design):
        qty = 7
        ladder = ProcessLadder(quantities=[qty])
        result = ladder.evaluate([cnc_design])
        for row in result.rows:
            for cell in row.cells:
                assert cell.total_cost_usd == pytest.approx(
                    cell.unit_cost_usd * qty, rel=1e-9
                )

    def test_unit_cost_stable_across_quantities(self, cnc_design):
        """unit_cost_usd should be the same regardless of the quantity tier."""
        ladder = ProcessLadder(quantities=[1, 100])
        result = ladder.evaluate([cnc_design])
        rows_by_qty: dict[int, list[QuantityRow]] = {}
        for row in result.rows:
            rows_by_qty.setdefault(row.quantity, []).append(row)
        # For each profile, unit cost at qty=1 == unit cost at qty=100
        rows_q1 = {c.profile_id: c.unit_cost_usd for row in rows_by_qty.get(1, []) for c in row.cells}
        rows_q100 = {c.profile_id: c.unit_cost_usd for row in rows_by_qty.get(100, []) for c in row.cells}
        for pid in rows_q1:
            if pid in rows_q100:
                assert rows_q1[pid] == pytest.approx(rows_q100[pid], rel=1e-9)

    def test_design_names_list_preserves_order(self, cnc_design, additive_design, sheet_design):
        designs = [cnc_design, additive_design, sheet_design]
        ladder = ProcessLadder(quantities=[10])
        result = ladder.evaluate(designs)
        assert result.designs == [d.name for d in designs]

    def test_three_designs_three_qtys(self, cnc_design, additive_design, sheet_design):
        ladder = ProcessLadder(quantities=[1, 25, 100])
        result = ladder.evaluate([cnc_design, additive_design, sheet_design])
        assert len(result.rows) == 9  # 3 × 3


# ---------------------------------------------------------------------------
# LadderResult query helpers
# ---------------------------------------------------------------------------


class TestLadderResultQueries:
    @pytest.fixture()
    def result(self, cnc_design, additive_design):
        ladder = ProcessLadder(quantities=[1, 50, 200])
        return ladder.evaluate([cnc_design, additive_design])

    def test_at_quantity_filters(self, result):
        rows = result.at_quantity(50)
        assert all(r.quantity == 50 for r in rows)
        assert len(rows) == 2  # one per design

    def test_at_quantity_sorted_by_name(self, result):
        rows = result.at_quantity(50)
        names = [r.design_name for r in rows]
        assert names == sorted(names)

    def test_at_quantity_missing_returns_empty(self, result):
        assert result.at_quantity(999) == []

    def test_for_design_filters(self, result):
        rows = result.for_design("cnc-bracket")
        assert all(r.design_name == "cnc-bracket" for r in rows)
        assert len(rows) == 3  # one per quantity

    def test_for_design_sorted_by_qty(self, result):
        rows = result.for_design("cnc-bracket")
        qtys = [r.quantity for r in rows]
        assert qtys == sorted(qtys)

    def test_for_design_unknown_returns_empty(self, result):
        assert result.for_design("no-such-design") == []

    def test_cheapest_design_at_qty(self, result):
        cheapest = result.cheapest_design_at_qty(50)
        assert cheapest is not None
        others = [r for r in result.at_quantity(50) if r.design_name != cheapest.design_name]
        for other in others:
            assert cheapest.cheapest_total_usd <= other.cheapest_total_usd

    def test_cheapest_design_at_missing_qty_returns_none(self, result):
        assert result.cheapest_design_at_qty(999) is None

    def test_cost_matrix_shape(self, result):
        matrix = result.cost_matrix()
        assert set(matrix.keys()) == {"cnc-bracket", "printed-housing"}
        for v in matrix.values():
            assert set(v.keys()) == {1, 50, 200}

    def test_cost_matrix_values_positive(self, result):
        for row in result.cost_matrix().values():
            for v in row.values():
                assert v > 0

    def test_as_dict_is_json_serializable(self, result):
        json.dumps(result.as_dict())  # must not raise

    def test_as_dict_keys(self, result):
        d = result.as_dict()
        assert "designs" in d
        assert "quantities" in d
        assert "rows" in d

    def test_text_table_contains_design_names(self, result):
        table = result.text_table()
        assert "cnc-bracket" in table
        assert "printed-housing" in table

    def test_text_table_contains_qty_headers(self, result):
        table = result.text_table()
        assert "qty=50" in table
        assert "qty=200" in table

    def test_text_table_empty_result(self):
        empty = LadderResult(designs=[], quantities=[10], rows=[])
        assert "no results" in empty.text_table()


# ---------------------------------------------------------------------------
# compare_designs convenience function
# ---------------------------------------------------------------------------


class TestCompareDesigns:
    def test_returns_ladder_result(self, cnc_design):
        result = compare_designs([cnc_design])
        assert isinstance(result, LadderResult)

    def test_default_quantities(self, cnc_design):
        result = compare_designs([cnc_design])
        assert set(result.quantities) == {1, 10, 50, 100}

    def test_custom_quantities(self, cnc_design):
        result = compare_designs([cnc_design], quantities=[5, 20])
        assert set(result.quantities) == {5, 20}

    def test_empty_designs(self):
        result = compare_designs([])
        assert result.designs == []
        assert result.rows == []

    def test_multiple_designs(self, cnc_design, additive_design):
        result = compare_designs([cnc_design, additive_design], quantities=[10])
        assert len(result.designs) == 2

    def test_profile_ids_restricts_bridge(self, cnc_design):
        """Restricting to a specific profile should still return a result (or empty)."""
        from makerbench.costing import list_profiles
        profiles = list_profiles()
        # Use just the first profile to confirm the filter plumbing works
        first_id = profiles[0]
        result = compare_designs([cnc_design], quantities=[1], profile_ids=[first_id])
        assert isinstance(result, LadderResult)
        # rows may have 0 or 1 cells depending on profile compatibility, not an error
