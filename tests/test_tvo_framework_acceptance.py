"""Acceptance-lock tests for the public TVO three-phase framework (#414)."""

from __future__ import annotations

from makerbench.tvo_framework import (
    ALL_PHASE_SPECS,
    PHASE1_NAME,
    PHASE1_SUBMETRICS,
    PHASE2_NAME,
    PHASE2_SUBMETRICS,
    PHASE3_NAME,
    PHASE3_STUB_REASON,
    PHASE3_SUBMETRICS,
    PhaseStatus,
    framework_result,
    phase1_result,
    phase2_result,
    phase3_result,
)


def test_story_414_formal_phase_contracts_are_public():
    by_id = {spec.phase_id: spec for spec in ALL_PHASE_SPECS}

    assert tuple(by_id) == (PHASE1_NAME, PHASE2_NAME, PHASE3_NAME)
    assert PHASE1_SUBMETRICS == ("geometric_integrity", "constraint_fulfillment")
    assert PHASE2_SUBMETRICS == ("post_processor_accuracy", "toolpath_safety")
    assert PHASE3_SUBMETRICS == ("routing_timing",)
    for spec in ALL_PHASE_SPECS:
        assert spec.pass_criterion
        assert "No-Touch Physical Pipeline" in spec.pipeline_position


def test_story_414_phase_helpers_encode_public_pass_fail_criteria():
    p1 = phase1_result(geometric_integrity=True, constraint_fulfillment=False)
    p2 = phase2_result(post_processor_accuracy=True, toolpath_safety=False)
    p3_pass = phase3_result(routing_timing_seconds=12.0, process_sla_seconds=60.0)
    p3_fail = phase3_result(routing_timing_seconds=90.0, process_sla_seconds=60.0)

    assert p1.status is PhaseStatus.FAIL
    assert p2.status is PhaseStatus.FAIL
    assert p3_pass.status is PhaseStatus.PASS
    assert p3_fail.status is PhaseStatus.FAIL


def test_story_414_private_weights_and_phase3_stub_boundary():
    p1 = phase1_result(geometric_integrity=True, constraint_fulfillment=True)
    p2 = phase2_result(post_processor_accuracy=True, toolpath_safety=True)
    p3 = phase3_result(routing_timing_seconds=None, process_sla_seconds=60.0)
    result = framework_result(p1, p2, p3)

    assert "StudioPipeline" in PHASE3_STUB_REASON
    assert "marketplace" in PHASE3_STUB_REASON
    assert p3.status is PhaseStatus.STUB
    assert p3.submetric_results["routing_timing"] is None
    assert result.private_weighted_score_available is False
    assert result.phases_passed == 2
    assert result.all_passed is False
