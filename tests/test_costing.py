"""Local manufacturing-cost adapter tests."""

import pytest

from makerbench.costing import (
    AdditivePrintCostingAdapter,
    CncCostingAdapter,
    CostingAdapter,
    GeometryCostMetrics,
    ManufacturingCostEstimate,
    ProcessRateProfile,
    SheetLaserCostingAdapter,
)


def test_cnc_costing_is_reproducible_and_itemized():
    metrics = GeometryCostMetrics(
        material_volume_mm3=12_000,
        removed_volume_mm3=3_000,
        hole_count=4,
        setup_count=1,
        tool_change_count=2,
    )
    profile = ProcessRateProfile(
        process_id="cnc_milling",
        profile_id="local-cnc-aluminum-v1",
        material_id="6061-aluminum",
        material_usd_per_cm3=0.2,
        machine_usd_per_hour=60.0,
        removal_rate_cm3_per_min=1.5,
        setup_fee_usd=10.0,
        setup_time_minutes=5,
        tool_change_time_minutes=1.5,
        hole_usd_each=0.25,
        min_job_fee_usd=25.0,
        assumptions=("shop rate includes operator time",),
    )

    estimate = CncCostingAdapter().estimate(metrics, profile)
    repeat = CncCostingAdapter().estimate(metrics, profile)

    assert estimate == repeat
    assert estimate.estimate_not_quote is True
    assert estimate.total_usd == 25.0
    assert [item.name for item in estimate.line_items] == [
        "material",
        "material removal time",
        "setup fee",
        "setup time",
        "tool changes",
        "hole operations",
        "minimum job fee adjustment",
    ]
    subtotals = {item.name: item.subtotal_usd for item in estimate.line_items}
    assert subtotals["material"] == 2.4
    assert subtotals["material removal time"] == 2.0
    assert subtotals["setup fee"] == 10.0
    assert subtotals["setup time"] == 5.0
    assert subtotals["tool changes"] == 3.0
    assert subtotals["hole operations"] == 1.0
    assert "not a vendor quote" in estimate.assumptions[0]
    assert estimate.as_dict()["line_items"][1]["formula"].startswith("removed_volume_cm3")


def test_sheet_laser_costing_uses_nesting_yield_cut_time_and_features():
    metrics = GeometryCostMetrics(
        sheet_area_mm2=20_000,
        sheet_nesting_yield=0.8,
        cut_length_mm=1_000,
        bend_count=3,
        hole_count=8,
        setup_count=1,
    )
    profile = ProcessRateProfile(
        process_id="sheet_laser",
        profile_id="sendcutsend-like-5052-v1",
        material_id="5052-h32-1.6mm",
        sheet_material_usd_per_cm2=0.03,
        machine_usd_per_hour=90.0,
        cut_speed_mm_per_min=500.0,
        setup_fee_usd=5.0,
        bend_usd_each=1.5,
        hole_usd_each=0.1,
    )

    estimate = SheetLaserCostingAdapter().estimate(metrics, profile)

    assert estimate.total_usd == 20.8
    assert {item.name: item.subtotal_usd for item in estimate.line_items} == {
        "nested sheet material": 7.5,
        "laser cutting time": 3.0,
        "setup fee": 5.0,
        "bend operations": 4.5,
        "hole operations": 0.8,
    }
    material = estimate.line_items[0]
    assert material.quantity == 250.0
    assert material.assumptions == ("nesting yield 0.800",)


def test_additive_print_costing_reports_material_support_time_and_overhead():
    metrics = GeometryCostMetrics(
        material_volume_mm3=30_000,
        support_material_volume_mm3=5_000,
        print_time_minutes=120,
        setup_count=1,
    )
    profile = ProcessRateProfile(
        process_id="additive_3d_print",
        profile_id="local-fdm-pla-v1",
        material_id="pla",
        material_usd_per_cm3=0.08,
        consumable_usd_per_cm3=0.04,
        print_usd_per_hour=6.0,
        setup_time_minutes=10,
        machine_usd_per_hour=12.0,
        overhead_multiplier=1.1,
    )

    estimate = AdditivePrintCostingAdapter().estimate(metrics, profile)

    assert estimate.total_usd == 18.26
    assert [item.name for item in estimate.line_items] == [
        "print material",
        "support material",
        "print time",
        "setup time",
        "overhead multiplier",
    ]
    assert estimate.line_items[-1].subtotal_usd == 1.66


def test_process_specific_subclass_can_extend_costing_adapter():
    class FixedFixtureAdapter(CostingAdapter):
        process_id = "cnc_milling"

        def estimate(self, metrics, profile):
            self._check_profile(profile)
            return ManufacturingCostEstimate(
                process_id=profile.process_id,
                profile_id=profile.profile_id,
                material_id=profile.material_id,
                currency=profile.currency,
                line_items=(),
                assumptions=("custom fixture adapter",),
            )

    profile = ProcessRateProfile(
        process_id="cnc_milling",
        profile_id="fixture-v1",
        material_id="delrin",
    )

    estimate = FixedFixtureAdapter().estimate(GeometryCostMetrics(), profile)

    assert estimate.total_usd == 0
    assert estimate.assumptions == ("custom fixture adapter",)


def test_costing_validates_inputs_and_profile_process():
    with pytest.raises(ValueError, match="material_volume_mm3"):
        GeometryCostMetrics(material_volume_mm3=-1)
    with pytest.raises(ValueError, match="sheet_nesting_yield"):
        GeometryCostMetrics(sheet_nesting_yield=1.5)
    with pytest.raises(ValueError, match="requires process_id"):
        CncCostingAdapter().estimate(
            GeometryCostMetrics(),
            ProcessRateProfile(
                process_id="sheet_laser",
                profile_id="wrong-process",
                material_id="steel",
            ),
        )
    with pytest.raises(ValueError, match="removal_rate_cm3_per_min"):
        CncCostingAdapter().estimate(
            GeometryCostMetrics(removed_volume_mm3=1000),
            ProcessRateProfile(
                process_id="cnc_milling",
                profile_id="missing-removal-rate",
                material_id="aluminum",
            ),
        )
