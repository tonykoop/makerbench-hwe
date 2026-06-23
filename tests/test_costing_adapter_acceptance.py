"""Issue-level acceptance lock for the deterministic CostingAdapter (#81)."""

from __future__ import annotations

from pathlib import Path

import pytest

from makerbench.costing import (
    AdditivePrintCostingAdapter,
    CncCostingAdapter,
    CostLineItem,
    CostingAdapter,
    GeometryCostMetrics,
    ManufacturingCostEstimate,
    ProcessRateProfile,
    SheetLaserCostingAdapter,
    adapter_for,
    estimate_cost,
    get_profile,
    list_profiles,
)


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "COSTING_ADAPTER.md"


def test_cnc_geometry_metrics_yield_reproducible_itemized_estimate():
    metrics = GeometryCostMetrics(
        material_volume_mm3=12_000.0,
        removed_volume_mm3=3_000.0,
        setup_count=1,
        tool_change_count=2,
        hole_count=4,
    )
    profile = ProcessRateProfile(
        process_id="cnc_milling",
        profile_id="local-cnc-aluminum-v1",
        material_id="6061-aluminum",
        material_usd_per_cm3=0.20,
        machine_usd_per_hour=60.0,
        removal_rate_cm3_per_min=1.5,
        setup_fee_usd=10.0,
        setup_time_minutes=5.0,
        tool_change_time_minutes=1.5,
        hole_usd_each=0.25,
        min_job_fee_usd=25.0,
    )

    estimate = CncCostingAdapter().estimate(metrics, profile)
    repeat = CncCostingAdapter().estimate(metrics, profile)

    assert estimate == repeat
    assert estimate.total_usd == 25.0
    assert estimate.estimate_not_quote is True
    assert [item.name for item in estimate.line_items] == [
        "material",
        "material removal time",
        "setup fee",
        "setup time",
        "tool changes",
        "hole operations",
        "minimum job fee adjustment",
    ]
    assert estimate.as_dict()["line_items"][1]["formula"].startswith(
        "removed_volume_cm3"
    )
    assert "not a vendor quote" in estimate.assumptions[0]


def test_sheet_laser_and_additive_profiles_use_process_specific_formulas():
    sheet = SheetLaserCostingAdapter().estimate(
        GeometryCostMetrics(
            sheet_area_mm2=20_000.0,
            sheet_nesting_yield=0.8,
            cut_length_mm=1_000.0,
            bend_count=3,
            hole_count=8,
        ),
        ProcessRateProfile(
            process_id="sheet_laser",
            profile_id="sheet-local-v1",
            material_id="5052-h32",
            sheet_material_usd_per_cm2=0.03,
            machine_usd_per_hour=90.0,
            cut_speed_mm_per_min=500.0,
            setup_fee_usd=5.0,
            bend_usd_each=1.5,
            hole_usd_each=0.1,
        ),
    )
    additive = AdditivePrintCostingAdapter().estimate(
        GeometryCostMetrics(
            material_volume_mm3=30_000.0,
            support_material_volume_mm3=5_000.0,
            print_time_minutes=120.0,
        ),
        ProcessRateProfile(
            process_id="additive_3d_print",
            profile_id="fdm-local-v1",
            material_id="pla",
            material_usd_per_cm3=0.08,
            consumable_usd_per_cm3=0.04,
            print_usd_per_hour=6.0,
            setup_time_minutes=10.0,
            machine_usd_per_hour=12.0,
        ),
    )

    assert {item.name for item in sheet.line_items} == {
        "nested sheet material",
        "laser cutting time",
        "setup fee",
        "bend operations",
        "hole operations",
    }
    assert {item.name for item in additive.line_items} == {
        "print material",
        "support material",
        "print time",
        "setup time",
    }
    assert sheet.estimate_not_quote is True
    assert additive.estimate_not_quote is True


def test_process_specific_subclass_can_extend_clean_interface():
    class FixtureCostingAdapter(CostingAdapter):
        process_id = "cnc_milling"

        def estimate(self, metrics, profile):
            self._check_profile(profile)
            return ManufacturingCostEstimate(
                process_id=profile.process_id,
                profile_id=profile.profile_id,
                material_id=profile.material_id,
                currency=profile.currency,
                line_items=(
                    CostLineItem(
                        name="fixture amortization",
                        category="fixture",
                        quantity=1,
                        unit="job",
                        unit_cost_usd=12.0,
                        subtotal_usd=12.0,
                        formula="fixed fixture charge",
                    ),
                ),
                assumptions=("custom process-specific subclass",),
            )

    profile = ProcessRateProfile(
        process_id="cnc_milling",
        profile_id="fixture-v1",
        material_id="delrin",
    )

    estimate = FixtureCostingAdapter().estimate(GeometryCostMetrics(), profile)

    assert estimate.total_usd == 12.0
    assert estimate.assumptions == ("custom process-specific subclass",)


def test_single_source_entrypoints_and_serialization_round_trip():
    profile = get_profile("fdm-pla-v1")
    metrics = GeometryCostMetrics(
        material_volume_mm3=18_000.0,
        support_material_volume_mm3=1_200.0,
        print_time_minutes=90.0,
    )

    assert "fdm-pla-v1" in list_profiles()
    assert adapter_for(profile.process_id).__class__ is AdditivePrintCostingAdapter
    assert estimate_cost(metrics, profile) == AdditivePrintCostingAdapter().estimate(
        metrics,
        profile,
    )

    metrics_payload = {
        **metrics.to_dict(),
        "ignored_downstream_annotation": "ok",
    }
    profile_payload = {
        **profile.to_dict(),
        "assumptions": list(profile.assumptions),
        "ignored_downstream_annotation": "ok",
    }
    assert GeometryCostMetrics.from_dict(metrics_payload) == metrics
    assert ProcessRateProfile.from_dict(profile_payload) == profile


def test_costing_validates_inputs_and_profile_matching():
    with pytest.raises(ValueError, match="material_volume_mm3"):
        GeometryCostMetrics(material_volume_mm3=-1.0)
    with pytest.raises(ValueError, match="sheet_nesting_yield"):
        GeometryCostMetrics(sheet_nesting_yield=0.0)
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
            GeometryCostMetrics(removed_volume_mm3=1_000.0),
            ProcessRateProfile(
                process_id="cnc_milling",
                profile_id="missing-rate",
                material_id="6061-aluminum",
            ),
        )


def test_docs_pin_manufacturing_cost_not_llm_cost_or_vendor_quote_boundary():
    doc = DOC.read_text(encoding="utf-8")
    normalized = " ".join(doc.split())

    for phrase in (
        "prices the produced part, not the LLM run",
        "it is not a quote",
        "itemized estimate",
        "Costing estimates are local, transparent, and reproducible",
        "live quote bridges remain a separate layer",
        "do not affect `GradeResult.score`",
    ):
        assert phrase in normalized
