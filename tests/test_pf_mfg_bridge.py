"""Tests for prototyping_funnel.mfg_bridge — manufacturing cost bridge.

Covers: DesignSpec, ManufacturingBridge, BridgeResult, ProcessCostResult.
Epic #240, story #81/#82 (CostingAdapter / manufacturing bridge).
"""

from __future__ import annotations

import json

import pytest

from prototyping_funnel.mfg_bridge import (
    BridgeResult,
    DesignSpec,
    ManufacturingBridge,
    ProcessCostResult,
)


# ---------------------------------------------------------------------------
# DesignSpec
# ---------------------------------------------------------------------------


def test_design_spec_basic():
    spec = DesignSpec(name="motor-mount", dfm_score=88.5, material_volume_mm3=10_000)
    assert spec.name == "motor-mount"
    assert spec.dfm_score == 88.5


def test_design_spec_rejects_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        DesignSpec(name="")


def test_design_spec_rejects_dfm_score_out_of_range():
    with pytest.raises(ValueError, match="dfm_score"):
        DesignSpec(name="part", dfm_score=105.0)


def test_design_spec_to_geometry_metrics_passthrough():
    spec = DesignSpec(
        name="bracket",
        material_volume_mm3=5_000,
        removed_volume_mm3=1_000,
        hole_count=4,
    )
    metrics = spec.to_geometry_metrics()
    assert metrics.material_volume_mm3 == 5_000
    assert metrics.removed_volume_mm3 == 1_000
    assert metrics.hole_count == 4


# ---------------------------------------------------------------------------
# ManufacturingBridge — full preset sweep
# ---------------------------------------------------------------------------


def _cnc_spec() -> DesignSpec:
    """A design shaped for CNC milling (has removed volume)."""
    return DesignSpec(
        name="cnc-bracket",
        dfm_score=92.0,
        material_volume_mm3=12_000,
        removed_volume_mm3=3_000,
        hole_count=6,
        setup_count=1,
        tool_change_count=2,
    )


def _sheet_spec() -> DesignSpec:
    """A design shaped for sheet laser (has sheet area + cut length)."""
    return DesignSpec(
        name="sheet-plate",
        dfm_score=85.0,
        sheet_area_mm2=25_000,
        cut_length_mm=1_200,
        bend_count=2,
        hole_count=4,
        sheet_nesting_yield=0.75,
        setup_count=1,
    )


def _print_spec() -> DesignSpec:
    """A design shaped for additive print."""
    return DesignSpec(
        name="fdm-bracket",
        dfm_score=78.0,
        material_volume_mm3=8_000,
        support_material_volume_mm3=1_500,
        print_time_minutes=90.0,
        setup_count=1,
    )


def test_bridge_returns_results_for_cnc_design():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    assert isinstance(result, BridgeResult)
    assert result.design_name == "cnc-bracket"
    assert len(result.results) > 0


def test_bridge_returns_results_for_sheet_design():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_sheet_spec())
    assert len(result.results) > 0


def test_bridge_returns_results_for_print_design():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_print_spec())
    assert len(result.results) > 0


def test_bridge_cheapest_has_rank_one():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    cheapest = result.cheapest()
    assert cheapest is not None
    assert cheapest.rank == 1


def test_bridge_results_are_ranked_ascending_by_cost():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    costs = [r.total_usd for r in result.results]
    assert costs == sorted(costs)


def test_bridge_ranks_are_contiguous_from_one():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    ranks = [r.rank for r in result.results]
    assert ranks == list(range(1, len(ranks) + 1))


def test_bridge_by_process_filter():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    cnc_results = result.by_process("cnc_milling")
    assert all(r.process_id == "cnc_milling" for r in cnc_results)


def test_bridge_result_as_dict_is_json_serializable():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    d = result.as_dict()
    json.dumps(d)
    assert d["design_name"] == "cnc-bracket"
    assert d["process_count"] == len(result.results)
    assert d["cheapest_profile_id"] == result.cheapest().profile_id


def test_bridge_restricted_to_specific_profiles():
    from makerbench.costing import list_profiles
    cnc_profiles = [p for p in list_profiles() if "cnc" in p]
    bridge = ManufacturingBridge(profile_ids=cnc_profiles)
    result = bridge.evaluate(_cnc_spec())
    assert all(r.process_id == "cnc_milling" for r in result.results)
    assert len(result.results) == len(cnc_profiles)


def test_bridge_profile_ids_property_matches_init():
    from makerbench.costing import list_profiles
    all_profiles = list_profiles()
    bridge = ManufacturingBridge()
    assert set(bridge.profile_ids) == set(all_profiles)


def test_bridge_evaluate_is_deterministic():
    bridge = ManufacturingBridge()
    spec = _cnc_spec()
    first = bridge.evaluate(spec).as_dict()
    second = bridge.evaluate(spec).as_dict()
    assert first == second


def test_bridge_cheapest_process_shortcut():
    bridge = ManufacturingBridge()
    cheapest = bridge.cheapest_process(_print_spec())
    assert cheapest is not None
    assert cheapest.rank == 1
    full_result = bridge.evaluate(_print_spec())
    assert cheapest.profile_id == full_result.cheapest().profile_id


def test_bridge_all_estimates_are_positive():
    bridge = ManufacturingBridge()
    for spec in [_cnc_spec(), _sheet_spec(), _print_spec()]:
        result = bridge.evaluate(spec)
        for r in result.results:
            assert r.total_usd > 0, f"{r.profile_id} returned zero cost"


def test_bridge_estimate_line_items_non_empty():
    bridge = ManufacturingBridge()
    result = bridge.evaluate(_cnc_spec())
    for r in result.results:
        assert len(r.estimate.line_items) > 0
