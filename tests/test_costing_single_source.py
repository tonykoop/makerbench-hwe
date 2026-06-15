"""Single-source CostingAdapter interface (issue #81 cross-team contract).

These cover the additions that let a downstream cost layer (e.g. HWE-Pipeline,
epic #24) single-source against `makerbench.costing` instead of re-deriving rate
tables and adapter wiring:

* round-trippable `to_dict`/`from_dict` on metrics + profiles,
* the `estimate_cost` / `adapter_for` dispatch entrypoint,
* the vendor-neutral `PROFILE_PRESETS` registry.
"""

from __future__ import annotations

import json

import pytest

from makerbench.costing import (
    ADAPTERS,
    CncCostingAdapter,
    GeometryCostMetrics,
    PROFILE_PRESETS,
    ProcessRateProfile,
    adapter_for,
    estimate_cost,
    get_profile,
    list_profiles,
)


def test_metrics_round_trip_through_json():
    metrics = GeometryCostMetrics(
        material_volume_mm3=12_000,
        removed_volume_mm3=3_000,
        hole_count=4,
        sheet_nesting_yield=0.8,
    )
    restored = GeometryCostMetrics.from_dict(json.loads(json.dumps(metrics.to_dict())))
    assert restored == metrics


def test_profile_round_trip_preserves_tuple_assumptions():
    profile = ProcessRateProfile(
        process_id="cnc_milling",
        profile_id="rt-v1",
        material_id="6061-aluminum",
        material_usd_per_cm3=0.2,
        machine_usd_per_hour=60.0,
        removal_rate_cm3_per_min=1.5,
        assumptions=("a", "b"),
    )
    payload = json.loads(json.dumps(profile.to_dict()))
    # assumptions survives the JSON list -> tuple restore.
    assert isinstance(payload["assumptions"], list)
    restored = ProcessRateProfile.from_dict(payload)
    assert restored == profile
    assert isinstance(restored.assumptions, tuple)


def test_from_dict_ignores_unknown_annotation_keys():
    # A downstream registry may carry extra metadata (label, vendor, notes).
    data = {
        "process_id": "cnc_milling",
        "profile_id": "extra-keys-v1",
        "material_id": "6061-aluminum",
        "material_usd_per_cm3": 0.2,
        "label": "Aluminum 3-axis (HWE-Pipeline)",
        "vendor_hint": "ignored",
    }
    profile = ProcessRateProfile.from_dict(data)
    assert profile.profile_id == "extra-keys-v1"
    assert not hasattr(profile, "label")


def test_estimate_cost_dispatches_to_matching_adapter():
    metrics = GeometryCostMetrics(material_volume_mm3=10_000, removed_volume_mm3=2_000)
    profile = get_profile("cnc-aluminum-3axis-v1")
    dispatched = estimate_cost(metrics, profile)
    direct = CncCostingAdapter().estimate(metrics, profile)
    assert dispatched.as_dict() == direct.as_dict()


def test_adapter_for_covers_every_known_process():
    for process_id in ADAPTERS:
        assert adapter_for(process_id).process_id == process_id


def test_adapter_for_rejects_unknown_process():
    with pytest.raises(ValueError, match="no costing adapter"):
        adapter_for("laser_welding")  # type: ignore[arg-type]


def test_presets_are_valid_and_dispatchable():
    assert set(list_profiles()) == set(PROFILE_PRESETS)
    metrics = GeometryCostMetrics(
        material_volume_mm3=8_000,
        removed_volume_mm3=1_500,
        cut_length_mm=600,
        sheet_area_mm2=20_000,
        bend_count=2,
        hole_count=3,
        print_time_minutes=120,
        support_material_volume_mm3=500,
    )
    for profile_id in list_profiles():
        profile = get_profile(profile_id)
        # Every preset must be vendor-neutral and labelled as an estimate.
        assert any("not a vendor quote" in a for a in profile.assumptions)
        estimate = estimate_cost(metrics, profile)
        assert estimate.estimate_not_quote is True
        assert estimate.total_usd >= 0


def test_get_profile_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown profile_id"):
        get_profile("nope-v0")
