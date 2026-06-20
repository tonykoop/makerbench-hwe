"""Tests for public TVO advanced Benchy process-track criteria."""

from __future__ import annotations

import dataclasses

import pytest

from makerbench.tvo_advanced_tracks import (
    DEFAULT_ADVANCED_BENCHY_TRACKS,
    INJECTION_MOLD_BENCHY_TRACK,
    METAL_LPBF_BENCHY_TRACK,
    AdvancedBenchyTrack,
    AdvancedTrackCriterion,
    SimulatorDependency,
    evaluate_advanced_benchy_track,
    validate_advanced_benchy_tracks,
)


def test_default_advanced_tracks_cover_injection_molding_and_metal_lpbf():
    tracks = validate_advanced_benchy_tracks()
    by_process = {track.process: track for track in tracks}

    assert set(by_process) == {"injection_molding", "metal_lpbf"}
    assert by_process["injection_molding"].track_id == "tvo_benchy_injection_mold"
    assert by_process["metal_lpbf"].track_id == "tvo_benchy_metal_lpbf"


def test_injection_mold_track_declares_required_criteria_and_simulator_cost():
    track = INJECTION_MOLD_BENCHY_TRACK
    criterion_ids = {criterion.criterion_id for criterion in track.criteria}

    assert {
        "draft_angle",
        "gate_balance",
        "cooling_channels",
        "parting_line",
        "moldflow_converged",
    }.issubset(criterion_ids)
    assert track.simulator_dependency.simulator_id == "deterministic_mold_flow_solver"
    assert track.simulator_dependency.dominant_build_cost is True
    assert track.public_criteria_private_weights is True


def test_metal_lpbf_track_declares_support_and_orientation_criteria():
    track = METAL_LPBF_BENCHY_TRACK
    criterion_ids = {criterion.criterion_id for criterion in track.criteria}

    assert {
        "sacrificial_supports",
        "orientation_downskin",
        "residual_stress_orientation",
        "thermal_hotspots",
        "lpbf_model_converged",
    }.issubset(criterion_ids)
    assert track.simulator_dependency.simulator_id == "deterministic_lpbf_thermal_stress_model"
    assert track.simulator_dependency.dominant_build_cost is True


def test_each_advanced_track_plugs_into_all_phase_2_submetrics():
    required = {
        "manufacturing_geometry",
        "process_physics_simulation",
        "tooling_or_support_strategy",
        "process_plan_integrity",
    }

    for track in DEFAULT_ADVANCED_BENCHY_TRACKS:
        covered = {criterion.phase2_submetric for criterion in track.criteria}
        assert covered == required
        assert track.phase == "phase_2_physical_reality_check"


def test_injection_mold_measurements_pass_public_criteria():
    report = evaluate_advanced_benchy_track(
        INJECTION_MOLD_BENCHY_TRACK,
        {
            "min_draft_angle_deg": 1.4,
            "gate_balance_index": 0.88,
            "cooling_uniformity_index": 0.81,
            "parting_line_self_intersections": 0.0,
            "moldflow_solver_converged": True,
        },
    )

    assert report.passed is True
    assert all(report.checks.values())
    assert report.missing_measurements == ()
    assert all(report.phase2_submetrics.values())
    assert report.as_dict()["profile_id"] == "tvo-advanced-benchy-process-tracks-v1"


def test_lpbf_measurements_fail_on_missing_supports_and_stress():
    report = evaluate_advanced_benchy_track(
        METAL_LPBF_BENCHY_TRACK,
        {
            "supported_overhang_fraction": 0.70,
            "min_downskin_angle_deg": 40.0,
            "residual_stress_ratio": 1.25,
            "thermal_hotspot_ratio": 0.90,
            "lpbf_model_converged": True,
        },
    )

    assert report.passed is False
    assert report.checks["sacrificial_supports"] is False
    assert report.checks["residual_stress_orientation"] is False
    assert report.phase2_submetrics["process_physics_simulation"] is False
    assert report.phase2_submetrics["tooling_or_support_strategy"] is False


def test_missing_measurements_fail_closed():
    report = evaluate_advanced_benchy_track(
        INJECTION_MOLD_BENCHY_TRACK,
        {"min_draft_angle_deg": 1.2},
    )

    assert report.passed is False
    assert set(report.missing_measurements) == {
        "gate_balance_index",
        "cooling_uniformity_index",
        "parting_line_self_intersections",
        "moldflow_solver_converged",
    }
    assert report.checks["draft_angle"] is True
    assert report.checks["gate_balance"] is False


def test_public_reports_expose_checks_not_scores_or_weights():
    report = evaluate_advanced_benchy_track(
        METAL_LPBF_BENCHY_TRACK,
        {
            "supported_overhang_fraction": 0.98,
            "min_downskin_angle_deg": 42.0,
            "residual_stress_ratio": 0.82,
            "thermal_hotspot_ratio": 0.77,
            "lpbf_model_converged": True,
        },
    )
    payload = report.as_dict()
    track_payload = METAL_LPBF_BENCHY_TRACK.as_dict()

    assert not hasattr(report, "total_score")
    assert "total_score" not in payload
    assert "score_weights" not in payload
    assert "score_weights" not in track_payload
    assert "criteria_weights" not in track_payload
    assert track_payload["public_criteria_private_weights"] is True


def test_track_validation_rejects_duplicate_or_incomplete_banks():
    duplicate = (INJECTION_MOLD_BENCHY_TRACK, INJECTION_MOLD_BENCHY_TRACK)
    with pytest.raises(ValueError, match="duplicate"):
        validate_advanced_benchy_tracks(duplicate)

    other_process = dataclasses.replace(METAL_LPBF_BENCHY_TRACK, process="ceramic_slip_cast")
    with pytest.raises(ValueError, match="injection molding and metal LPBF"):
        validate_advanced_benchy_tracks((INJECTION_MOLD_BENCHY_TRACK, other_process))


def test_dataclasses_fail_closed_on_bad_phase_or_dependency_metric():
    with pytest.raises(ValueError, match="Phase 2"):
        dataclasses.replace(INJECTION_MOLD_BENCHY_TRACK, phase="phase_1_intent_capture")

    with pytest.raises(ValueError, match="unknown metric"):
        dataclasses.replace(
            INJECTION_MOLD_BENCHY_TRACK,
            simulator_dependency=SimulatorDependency(
                simulator_id="bad_solver",
                purpose="references a metric the criteria do not provide",
                required_metrics=("not_declared",),
            ),
        )


def test_numeric_criteria_reject_bool_and_string_measurements():
    criterion = AdvancedTrackCriterion(
        criterion_id="numeric",
        title="Numeric criterion",
        phase2_submetric="manufacturing_geometry",
        measurement_key="value",
        comparator="gte",
        target=1.0,
        description="Requires a numeric measurement.",
    )

    assert criterion.evaluate({"value": True}) is False
    assert criterion.evaluate({"value": "1.2"}) is False


def test_track_requires_at_least_one_simulator_metric():
    with pytest.raises(ValueError, match="simulator metric"):
        AdvancedBenchyTrack(
            track_id="bad",
            title="Bad",
            process="bad_process",
            phase="phase_2_physical_reality_check",
            simulator_dependency=SimulatorDependency(
                simulator_id="solver",
                purpose="exercise validation",
                required_metrics=("metric",),
            ),
            criteria=(
                AdvancedTrackCriterion(
                    criterion_id="metric",
                    title="Metric",
                    phase2_submetric="manufacturing_geometry",
                    measurement_key="metric",
                    comparator="eq",
                    target=True,
                    description="No simulator metric flag.",
                    simulator_metric=False,
                ),
            ),
        )
