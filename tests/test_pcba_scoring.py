"""Deterministic PCBA scoring profile tests (#212)."""

from __future__ import annotations

import pytest

from makerbench.pcba_scoring import (
    PROFILE_NAME,
    PCBADesignVelocity,
    PCBAMetrics,
    PCBAPowerNetRequirement,
    PCBAScoringProfile,
    PCBAThermalSource,
    score_pcba,
)


def _good_metrics() -> PCBAMetrics:
    return PCBAMetrics(
        board_area_mm2=900.0,
        occupied_area_mm2=180.0,
        component_count=12,
        smd_pad_count=42,
        through_hole_pin_count=8,
        via_count=10,
        copper_layer_count=2,
        power_nets=(
            PCBAPowerNetRequirement(
                name="3V3",
                current_ma=300.0,
                trace_length_mm=55.0,
                min_trace_width_mm=0.50,
                via_count=1,
                min_clearance_mm=0.25,
            ),
        ),
    )


def test_pcba_profile_scores_reproducibly_and_itemizes_cost():
    first = score_pcba(_good_metrics())
    repeat = score_pcba(_good_metrics())

    assert first == repeat
    assert first.profile_id == PROFILE_NAME
    assert first.total_score == 1.0
    assert first.cost_usd == 5.0
    assert first.checks["cost_under_max"] is True
    assert first.checks["3v3_vdrop_within_limit"] is True
    assert first.quality["worst_power_vdrop_mv"] == 16.5
    assert [item.name for item in first.line_items] == [
        "fabricated board area",
        "assembly setup",
        "component placement",
        "SMD pad inspection",
        "through-hole pin soldering",
        "via processing",
        "minimum job fee adjustment",
    ]
    assert first.as_dict()["assumptions"][0].startswith("deterministic PCBA estimate")


def test_cost_score_degrades_for_large_expensive_boards_without_hiding_pass_checks():
    metrics = PCBAMetrics(
        board_area_mm2=2_400.0,
        occupied_area_mm2=480.0,
        component_count=160,
        smd_pad_count=520,
        through_hole_pin_count=64,
        via_count=260,
        copper_layer_count=4,
        power_nets=(
            PCBAPowerNetRequirement(
                name="VBUS",
                current_ma=500.0,
                trace_length_mm=30.0,
                min_trace_width_mm=1.0,
                via_count=2,
                min_clearance_mm=0.3,
            ),
        ),
    )

    result = score_pcba(metrics)

    assert result.cost_usd == 14.268
    assert result.cost_score == pytest.approx(0.477667)
    assert result.checks["cost_within_target"] is False
    assert result.checks["cost_under_max"] is True
    assert result.power_integrity_score == 1.0


def test_compactness_profile_penalizes_oversized_and_overcrowded_layouts():
    oversized = score_pcba(PCBAMetrics(
        board_area_mm2=2_500.0,
        occupied_area_mm2=250.0,
        component_count=5,
        power_nets=(
            PCBAPowerNetRequirement("PWR", 100.0, 20.0, 0.4, min_clearance_mm=0.25),
        ),
    ))
    crowded = score_pcba(PCBAMetrics(
        board_area_mm2=900.0,
        occupied_area_mm2=720.0,
        component_count=80,
        power_nets=(
            PCBAPowerNetRequirement("PWR", 100.0, 20.0, 0.4, min_clearance_mm=0.25),
        ),
    ))

    assert oversized.checks["board_area_within_target"] is False
    assert oversized.compactness_score == 0.5
    assert crowded.checks["placement_fill_in_range"] is False
    assert crowded.compactness_score < 0.8


def test_power_integrity_flags_voltage_drop_current_density_clearance_and_via_capacity():
    metrics = PCBAMetrics(
        board_area_mm2=800.0,
        occupied_area_mm2=160.0,
        component_count=10,
        via_count=1,
        power_nets=(
            PCBAPowerNetRequirement(
                name="motor rail",
                current_ma=1_200.0,
                trace_length_mm=80.0,
                min_trace_width_mm=0.30,
                via_count=1,
                min_clearance_mm=0.10,
            ),
        ),
    )

    result = score_pcba(metrics)

    assert result.power_integrity_score == 0.0
    assert result.checks["motor_rail_vdrop_within_limit"] is False
    assert result.checks["motor_rail_current_density_within_limit"] is False
    assert result.checks["motor_rail_clearance_within_limit"] is False
    assert result.checks["motor_rail_via_current_within_limit"] is False
    assert result.quality["worst_power_vdrop_mv"] == 160.0
    assert result.quality["min_via_current_margin_ma"] == -700.0


def test_profile_validation_and_metric_validation_fail_closed():
    with pytest.raises(ValueError, match="board_area_mm2"):
        PCBAMetrics(board_area_mm2=0.0, component_count=1)
    with pytest.raises(ValueError, match="occupied_area_mm2"):
        PCBAMetrics(board_area_mm2=10.0, occupied_area_mm2=11.0, component_count=1)
    with pytest.raises(ValueError, match="min_trace_width_mm"):
        PCBAPowerNetRequirement("PWR", current_ma=1.0, trace_length_mm=1.0, min_trace_width_mm=0.0)
    with pytest.raises(ValueError, match="max_cost_usd"):
        PCBAScoringProfile(target_cost_usd=10.0, max_cost_usd=10.0)
    with pytest.raises(ValueError, match="max_design_iterations"):
        PCBAScoringProfile(target_design_iterations=12, max_design_iterations=12)
    with pytest.raises(ValueError, match="thermal source ref"):
        PCBAThermalSource("", power_w=1.0, theta_ja_c_per_w=40.0, max_junction_c=125.0)


def test_optional_dimensions_default_to_neutral_and_do_not_change_total():
    # No thermal sources / no design velocity -> both score 1.0 and the weighted
    # total is unchanged from the cost+compactness+power baseline.
    result = score_pcba(_good_metrics())
    assert result.total_score == 1.0
    assert result.thermal_score == 1.0
    assert result.design_velocity_score == 1.0
    assert result.checks["thermal_sources_declared"] is False
    assert result.checks["design_velocity_declared"] is False


def test_thermal_dimension_scores_junction_headroom_and_flags_overheat():
    metrics = PCBAMetrics(
        board_area_mm2=900.0,
        occupied_area_mm2=180.0,
        component_count=12,
        power_nets=(PCBAPowerNetRequirement("3V3", 100.0, 20.0, 0.5, min_clearance_mm=0.3),),
        thermal_sources=(
            PCBAThermalSource(
                "U1_buck", power_w=3.0, theta_ja_c_per_w=60.0, max_junction_c=150.0
            ),
        ),
    )
    result = score_pcba(metrics)
    # Tj = 25 + 3*60 = 205 C > 150 C limit.
    assert result.quality["worst_junction_temp_c"] == 205.0
    assert result.checks["u1_buck_junction_within_limit"] is False
    assert result.thermal_score == 0.0
    assert result.total_score < 1.0


def test_thermal_isolation_flags_sensitive_part_next_to_switcher():
    near = PCBAMetrics(
        board_area_mm2=900.0,
        occupied_area_mm2=180.0,
        component_count=12,
        power_nets=(PCBAPowerNetRequirement("3V3", 100.0, 20.0, 0.5, min_clearance_mm=0.3),),
        thermal_sources=(
            PCBAThermalSource("U1_buck", power_w=1.2, theta_ja_c_per_w=30.0,
                              max_junction_c=150.0, x_mm=10.0, y_mm=10.0, hot=True),
            PCBAThermalSource("Y1_xtal", power_w=0.0, theta_ja_c_per_w=200.0,
                              max_junction_c=85.0, x_mm=12.0, y_mm=10.0, sensitive=True),
        ),
    )
    result = score_pcba(near)
    assert result.checks["sensitive_parts_thermally_isolated"] is False
    assert result.quality["min_thermal_isolation_mm"] == 2.0

    far = PCBAMetrics(
        board_area_mm2=900.0,
        occupied_area_mm2=180.0,
        component_count=12,
        power_nets=(PCBAPowerNetRequirement("3V3", 100.0, 20.0, 0.5, min_clearance_mm=0.3),),
        thermal_sources=(
            PCBAThermalSource("U1_buck", power_w=1.2, theta_ja_c_per_w=30.0,
                              max_junction_c=150.0, x_mm=10.0, y_mm=10.0, hot=True),
            PCBAThermalSource("Y1_xtal", power_w=0.0, theta_ja_c_per_w=200.0,
                              max_junction_c=85.0, x_mm=30.0, y_mm=10.0, sensitive=True),
        ),
    )
    assert score_pcba(far).checks["sensitive_parts_thermally_isolated"] is True


def test_design_velocity_rewards_fast_convergence_and_penalizes_dirty_boards():
    fast = score_pcba(PCBAMetrics(
        board_area_mm2=900.0, occupied_area_mm2=180.0, component_count=12,
        power_nets=(PCBAPowerNetRequirement("3V3", 100.0, 20.0, 0.5, min_clearance_mm=0.3),),
        design_velocity=PCBADesignVelocity(iterations_to_clean=2),
    ))
    assert fast.design_velocity_score == 1.0
    assert fast.checks["design_within_iteration_budget"] is True

    never_clean = score_pcba(PCBAMetrics(
        board_area_mm2=900.0, occupied_area_mm2=180.0, component_count=12,
        power_nets=(PCBAPowerNetRequirement("3V3", 100.0, 20.0, 0.5, min_clearance_mm=0.3),),
        design_velocity=PCBADesignVelocity(iterations_to_clean=20, clean_achieved=False),
    ))
    assert never_clean.design_velocity_score == 0.0
    assert never_clean.checks["design_reached_clean"] is False
    assert never_clean.total_score < 1.0


def test_all_five_scoring_dimensions_are_bounded_and_deterministic():
    # Mixed fixture: non-maxed inputs so all five dimensions have non-trivial values.
    metrics = PCBAMetrics(
        board_area_mm2=1_600.0,
        occupied_area_mm2=400.0,
        component_count=50,
        smd_pad_count=120,
        through_hole_pin_count=16,
        via_count=8,
        copper_layer_count=2,
        power_nets=(
            PCBAPowerNetRequirement(
                name="5V",
                current_ma=500.0,
                trace_length_mm=80.0,
                min_trace_width_mm=0.40,
                via_count=1,
                min_clearance_mm=0.20,
            ),
        ),
        design_velocity=PCBADesignVelocity(iterations_to_clean=6),
    )
    first = score_pcba(metrics)
    second = score_pcba(metrics)

    # Determinism: identical inputs must produce bit-identical score vectors.
    assert first.cost_score == second.cost_score
    assert first.compactness_score == second.compactness_score
    assert first.power_integrity_score == second.power_integrity_score
    assert first.thermal_score == second.thermal_score
    assert first.design_velocity_score == second.design_velocity_score
    assert first.total_score == second.total_score

    # All five dimensions must be in [0.0, 1.0].
    for name, value in [
        ("cost", first.cost_score),
        ("compactness", first.compactness_score),
        ("power_integrity", first.power_integrity_score),
        ("thermal", first.thermal_score),
        ("design_velocity", first.design_velocity_score),
    ]:
        assert 0.0 <= value <= 1.0, f"{name}_score={value!r} outside [0, 1]"

    assert 0.0 <= first.total_score <= 1.0
