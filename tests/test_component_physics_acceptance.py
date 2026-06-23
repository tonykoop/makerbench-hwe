"""Acceptance locks for PCBA component physics primitives (#211)."""

from __future__ import annotations

from pathlib import Path

import pytest

from makerbench.component_physics import (
    DEFAULT_MAX_TRACE_RISE_C,
    MIL_PER_MM,
    component_from_mapping,
    thermal_calc,
    trace_width_calc,
)


ROOT = Path(__file__).resolve().parents[1]


def test_component_physics_metadata_carries_material_limits_and_package_fields():
    component = component_from_mapping(
        {
            "component_id": "U_SWITCH",
            "family": "load-switch",
            "manufacturer_part_number": "TPS22919",
            "value": "2A load switch",
            "package": {
                "package": "WSON-6",
                "pin_count": 6,
                "mount": "smt",
                "pitch_mm": 0.5,
            },
            "limits": {
                "max_current_a": 2.0,
                "thermal_resistance_c_per_w": 62.0,
                "max_junction_c": 150.0,
            },
            "material": {
                "package_material": "epoxy mold compound",
                "thermal_conductivity_w_mk": 0.9,
                "lead_composition": "SAC305",
                "recommended_solder_profile": "IPC-J-STD-020E lead-free",
            },
        }
    )

    payload = component.to_dict()
    assert payload["package"]["package"] == "WSON-6"
    assert payload["limits"]["thermal_resistance_c_per_w"] == 62.0
    assert payload["limits"]["max_junction_c"] == 150.0
    assert payload["material"]["package_material"] == "epoxy mold compound"
    assert payload["material"]["thermal_conductivity_w_mk"] == 0.9
    assert payload["material"]["lead_composition"] == "SAC305"
    assert payload["material"]["solder_profile"] == "IPC-J-STD-020E lead-free"


def test_trace_width_and_thermal_primitives_flag_story_fixtures():
    overloaded = trace_width_calc(
        current_a=3.0,
        width_mm=10 / MIL_PER_MM,
        copper_weight_oz=1.0,
    )
    assert overloaded["temperature_rise_c"] > 45.0
    assert overloaded["passed"] is False

    required = trace_width_calc(
        current_a=3.0,
        width_mm=overloaded["required_width_mm"],
        copper_weight_oz=1.0,
    )
    assert required["temperature_rise_c"] == pytest.approx(DEFAULT_MAX_TRACE_RISE_C)
    assert required["passed"] is True

    thermal = thermal_calc(
        current_a=5.0,
        r_ds_on_ohm=0.05,
        thermal_resistance_c_per_w=62.0,
        ambient_c=25.0,
        max_junction_c=150.0,
    )
    assert thermal["power_w"] == pytest.approx(1.25)
    assert thermal["junction_temp_c"] == pytest.approx(102.5)
    assert thermal["passed"] is True


def test_dfm_rules_document_pcba_physics_formulas_and_thresholds():
    text = (ROOT / "docs" / "DFM_RULES.md").read_text(encoding="utf-8")

    for phrase in (
        "package material",
        "thermal_conductivity",
        "max_junction_temp",
        "lead composition",
        "solder profile",
        "R_thetaJA",
        "trace_width_calc",
        "thermal_calc",
        "I = k",
        "P = I²",
        "ΔT ≤ `max_temp_rise_c`",
        "`Tj ≤ max_junction_c`",
    ):
        assert phrase in text
