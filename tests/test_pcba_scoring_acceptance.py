"""Acceptance locks for the PCBA scoring profile (#212)."""

from __future__ import annotations

from pathlib import Path

from makerbench.pcba_scoring import (
    PCBADesignVelocity,
    PCBAMetrics,
    PCBAPowerNetRequirement,
    PCBAThermalSource,
    score_pcba,
)


ROOT = Path(__file__).resolve().parents[1]


def test_pcba_profile_reports_all_five_dimensions_with_checks_and_quality():
    result = score_pcba(
        PCBAMetrics(
            board_area_mm2=900.0,
            occupied_area_mm2=180.0,
            component_count=12,
            smd_pad_count=42,
            through_hole_pin_count=8,
            via_count=10,
            copper_layer_count=2,
            power_nets=(
                PCBAPowerNetRequirement(
                    "3V3",
                    current_ma=300.0,
                    trace_length_mm=55.0,
                    min_trace_width_mm=0.50,
                    via_count=1,
                    min_clearance_mm=0.25,
                ),
            ),
            thermal_sources=(
                PCBAThermalSource(
                    "U1_buck",
                    power_w=0.6,
                    theta_ja_c_per_w=45.0,
                    max_junction_c=125.0,
                    x_mm=0.0,
                    y_mm=0.0,
                    hot=True,
                ),
                PCBAThermalSource(
                    "Y1_xtal",
                    power_w=0.0,
                    theta_ja_c_per_w=200.0,
                    max_junction_c=85.0,
                    x_mm=8.0,
                    y_mm=0.0,
                    sensitive=True,
                ),
            ),
            design_velocity=PCBADesignVelocity(iterations_to_clean=4),
        )
    )

    payload = result.as_dict()
    for key in (
        "cost_score",
        "compactness_score",
        "power_integrity_score",
        "thermal_score",
        "design_velocity_score",
    ):
        assert key in payload
        assert 0.0 <= payload[key] <= 1.0

    assert result.checks["cost_under_max"] is True
    assert result.checks["placement_fill_in_range"] is True
    assert result.checks["3v3_vdrop_within_limit"] is True
    assert result.checks["u1_buck_junction_within_limit"] is True
    assert result.checks["sensitive_parts_thermally_isolated"] is True
    assert result.checks["design_reached_clean"] is True
    assert result.quality["worst_power_vdrop_mv"] == 16.5
    assert result.quality["worst_junction_temp_c"] == 52.0
    assert result.quality["design_iterations"] == 4.0
    assert payload["line_items"]
    assert payload["assumptions"][0].startswith("deterministic PCBA estimate")


def test_pcba_scoring_docs_cover_acceptance_dimensions_and_reporting_contract():
    text = (ROOT / "docs" / "PCBA_SCORING.md").read_text(encoding="utf-8").lower()

    for phrase in (
        "cost",
        "compactness",
        "power integrity",
        "thermal",
        "design velocity",
        "continuous score",
        "pass/fail checks",
        "no vendor quotes",
        "no spice",
        "no llm judge",
    ):
        assert phrase in text
    assert "alongside" in text
    assert "four levels" in text
