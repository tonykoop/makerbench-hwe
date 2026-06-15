"""PCBA component physics metadata and deterministic primitive tests."""

from __future__ import annotations

import math

import pytest

from makerbench.component_physics import (
    ComponentPhysics,
    ElectricalThermalLimits,
    PackageMetadata,
    check_component_limits,
    component_from_mapping,
    derated_power_limit_w,
    resistor_current_for_power_a,
    resistor_power_w,
    resistor_voltage_for_power_v,
    thermal_power_limit,
    trace_current_capacity_a,
    trace_required_width_mm,
)


def test_package_metadata_records_public_pcba_shape():
    pkg = PackageMetadata(
        package=" 0603 ",
        pin_count=2,
        pitch_mm=0.8,
        body_length_mm=1.6,
        body_width_mm=0.8,
        notes=["metric 1608"],
    )

    assert pkg.package == "0603"
    assert pkg.pin_count == 2
    assert pkg.mount == "smt"
    assert pkg.to_dict() == {
        "package": "0603",
        "pin_count": 2,
        "mount": "smt",
        "pitch_mm": 0.8,
        "body_length_mm": 1.6,
        "body_width_mm": 0.8,
        "notes": ["metric 1608"],
    }


def test_component_mapping_round_trip_is_json_style():
    component = component_from_mapping(
        {
            "component_id": "R_LED",
            "family": "resistor",
            "value": "330 ohm",
            "package": {"package": "0603", "pin_count": 2, "pitch_mm": 0.8},
            "limits": {"max_power_w": 0.1, "max_voltage_v": 75},
        }
    )

    assert component.family == "resistor"
    assert component.package.pin_count == 2
    assert component.to_dict()["limits"] == {"max_voltage_v": 75.0, "max_power_w": 0.1}


def test_resistor_power_supports_voltage_current_and_limit_inverses():
    assert resistor_power_w(resistance_ohm=1000.0, voltage_v=5.0) == pytest.approx(0.025)
    assert resistor_power_w(resistance_ohm=1000.0, current_a=0.005) == pytest.approx(0.025)
    assert resistor_power_w(
        resistance_ohm=1000.0,
        voltage_v=5.0,
        current_a=0.005,
    ) == pytest.approx(0.025)
    assert resistor_current_for_power_a(resistance_ohm=1000.0, power_w=0.25) == pytest.approx(
        0.0158113883
    )
    assert resistor_voltage_for_power_v(resistance_ohm=1000.0, power_w=0.25) == pytest.approx(
        15.8113883
    )


def test_resistor_power_rejects_inconsistent_or_missing_inputs():
    with pytest.raises(ValueError, match="provide voltage_v or current_a"):
        resistor_power_w(resistance_ohm=1000.0)
    with pytest.raises(ValueError, match="inconsistent"):
        resistor_power_w(resistance_ohm=1000.0, voltage_v=5.0, current_a=0.010)
    with pytest.raises(ValueError, match="positive"):
        resistor_power_w(resistance_ohm=0.0, voltage_v=5.0)


def test_thermal_limit_and_linear_derating_are_deterministic():
    assert thermal_power_limit(
        max_junction_c=125,
        ambient_c=85,
        thermal_resistance_c_per_w=80,
    ) == pytest.approx(0.5)
    assert thermal_power_limit(
        max_junction_c=100,
        ambient_c=125,
        thermal_resistance_c_per_w=50,
    ) == 0.0
    assert derated_power_limit_w(
        rated_power_w=0.25,
        ambient_c=100,
        derate_from_c=70,
        zero_power_c=155,
    ) == pytest.approx(0.1617647059)


def test_trace_current_capacity_matches_ipc2221_reference_value_and_inverse():
    external = trace_current_capacity_a(
        width_mm=0.25,
        copper_thickness_um=35,
        temperature_rise_c=10,
    )
    internal = trace_current_capacity_a(
        width_mm=0.25,
        copper_thickness_um=35,
        temperature_rise_c=10,
        layer="internal",
    )

    assert external == pytest.approx(0.875363, rel=1e-6)
    assert internal == pytest.approx(external / 2.0)

    required = trace_required_width_mm(
        current_a=external,
        copper_thickness_um=35,
        temperature_rise_c=10,
    )
    assert required == pytest.approx(0.25, rel=1e-12)


def test_component_limit_check_reports_package_pin_count_and_margins():
    component = ComponentPhysics(
        component_id="U_LDO",
        family="regulator",
        package=PackageMetadata(package="SOT-23-5", pin_count=5),
        limits=ElectricalThermalLimits(
            max_voltage_v=6.0,
            max_current_a=0.15,
            max_power_w=0.5,
            thermal_resistance_c_per_w=250,
            max_junction_c=125,
        ),
    )

    ok = check_component_limits(component, voltage_v=5.0, current_a=0.1, power_w=0.25, ambient_c=50)
    assert ok["passed"] is True
    assert ok["pin_count"] == 5
    assert ok["margins"]["power_w"] == pytest.approx(0.05)

    hot = check_component_limits(component, voltage_v=5.0, current_a=0.1, power_w=0.4, ambient_c=50)
    assert hot["passed"] is False
    assert hot["checks"]["power_within_limit"] is False


def test_validation_rejects_invalid_metadata_and_layers():
    with pytest.raises(ValueError, match="pin_count"):
        PackageMetadata(package="QFN", pin_count=0)
    with pytest.raises(ValueError, match="mount"):
        PackageMetadata(package="QFN", pin_count=16, mount="flying")
    with pytest.raises(ValueError, match="layer"):
        trace_current_capacity_a(width_mm=0.25, layer="buried-ish")
    with pytest.raises(ValueError, match="zero_power_c"):
        derated_power_limit_w(
            rated_power_w=0.25,
            ambient_c=80,
            derate_from_c=100,
            zero_power_c=80,
        )


def test_calculators_never_return_nan_for_valid_boundary_inputs():
    values = [
        resistor_power_w(resistance_ohm=1e9, voltage_v=0),
        thermal_power_limit(max_junction_c=25, ambient_c=25, thermal_resistance_c_per_w=100),
        trace_current_capacity_a(width_mm=0.01, copper_thickness_um=1.0, temperature_rise_c=0.1),
    ]

    assert all(math.isfinite(value) for value in values)
