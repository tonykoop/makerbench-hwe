import pytest

from makerbench.tvo_framework import (
    ALL_PHASE_SPECS,
    ALL_PHASES,
    PHASE1_NAME,
    PHASE1_SPEC,
    PHASE1_SUBMETRICS,
    PHASE2_NAME,
    PHASE2_SPEC,
    PHASE2_SUBMETRICS,
    PHASE3_NAME,
    PHASE3_SPEC,
    PHASE3_STUB_REASON,
    PHASE3_SUBMETRICS,
    PhaseStatus,
    TVOFrameworkResult,
    framework_result,
    phase1_result,
    phase2_result,
    phase3_result,
)


# ---------------------------------------------------------------------------
# Phase constants
# ---------------------------------------------------------------------------


def test_phase_names_are_correct():
    assert PHASE1_NAME == "contextual_intent_capture"
    assert PHASE2_NAME == "physical_reality_check"
    assert PHASE3_NAME == "algorithmic_handshake"
    assert ALL_PHASES == (PHASE1_NAME, PHASE2_NAME, PHASE3_NAME)


def test_phase1_has_two_named_submetrics():
    assert set(PHASE1_SUBMETRICS) == {"geometric_integrity", "constraint_fulfillment"}


def test_phase2_has_two_named_submetrics():
    assert set(PHASE2_SUBMETRICS) == {"post_processor_accuracy", "toolpath_safety"}


def test_phase3_has_routing_timing_submetric():
    assert "routing_timing" in PHASE3_SUBMETRICS


# ---------------------------------------------------------------------------
# Phase specs document the pipeline and pass/fail criteria
# ---------------------------------------------------------------------------


def test_all_phase_specs_reference_no_touch_pipeline():
    for spec in ALL_PHASE_SPECS:
        assert "pipeline" in spec.pipeline_position.lower() or "pipeline" in spec.pass_criterion.lower() or "pipeline" in spec.pipeline_position.lower()
        assert spec.phase_id in ALL_PHASES
        assert len(spec.submetrics) >= 1
        assert spec.pass_criterion


def test_phase3_spec_is_stubbable_and_references_studio_pipeline():
    assert PHASE3_SPEC.stubbable is True
    assert "StudioPipeline" in PHASE3_STUB_REASON
    assert "stub" in PHASE3_STUB_REASON.lower()


def test_phase_specs_as_dict_exposes_required_fields():
    for spec in ALL_PHASE_SPECS:
        d = spec.as_dict()
        assert "phase_id" in d
        assert "title" in d
        assert "pipeline_position" in d
        assert "submetrics" in d
        assert "pass_criterion" in d
        assert "stubbable" in d
    # Phase 3 stub_reason is included
    d3 = PHASE3_SPEC.as_dict()
    assert "stub_reason" in d3


def test_phase1_spec_not_stubbable():
    assert PHASE1_SPEC.stubbable is False


def test_phase2_spec_not_stubbable():
    assert PHASE2_SPEC.stubbable is False


# ---------------------------------------------------------------------------
# phase1_result helper
# ---------------------------------------------------------------------------


def test_phase1_result_passes_when_both_true():
    r = phase1_result(geometric_integrity=True, constraint_fulfillment=True)
    assert r.status == PhaseStatus.PASS
    assert r.phase_id == PHASE1_NAME
    assert r.submetric_results["geometric_integrity"] is True
    assert r.submetric_results["constraint_fulfillment"] is True


@pytest.mark.parametrize(
    ("geo", "constraint"),
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
def test_phase1_result_fails_when_any_false(geo, constraint):
    r = phase1_result(geometric_integrity=geo, constraint_fulfillment=constraint)
    assert r.status == PhaseStatus.FAIL


# ---------------------------------------------------------------------------
# phase2_result helper
# ---------------------------------------------------------------------------


def test_phase2_result_passes_when_both_true():
    r = phase2_result(post_processor_accuracy=True, toolpath_safety=True)
    assert r.status == PhaseStatus.PASS
    assert r.phase_id == PHASE2_NAME


@pytest.mark.parametrize(
    ("pp_accuracy", "tp_safety"),
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
def test_phase2_result_fails_when_any_false(pp_accuracy, tp_safety):
    r = phase2_result(post_processor_accuracy=pp_accuracy, toolpath_safety=tp_safety)
    assert r.status == PhaseStatus.FAIL


# ---------------------------------------------------------------------------
# phase3_result helper
# ---------------------------------------------------------------------------


def test_phase3_result_stub_when_timing_is_none():
    r = phase3_result(routing_timing_seconds=None, process_sla_seconds=3600.0)
    assert r.status == PhaseStatus.STUB
    assert r.submetric_results["routing_timing"] is None
    assert r.phase_id == PHASE3_NAME


def test_phase3_result_passes_within_sla():
    r = phase3_result(routing_timing_seconds=1800.0, process_sla_seconds=3600.0)
    assert r.status == PhaseStatus.PASS
    assert r.submetric_results["routing_timing"] == 1800.0


def test_phase3_result_fails_when_exceeds_sla():
    r = phase3_result(routing_timing_seconds=7200.0, process_sla_seconds=3600.0)
    assert r.status == PhaseStatus.FAIL


# ---------------------------------------------------------------------------
# TVOFrameworkResult aggregate
# ---------------------------------------------------------------------------


def _make_full_result(p3_timing: float | None = None) -> TVOFrameworkResult:
    p1 = phase1_result(geometric_integrity=True, constraint_fulfillment=True)
    p2 = phase2_result(post_processor_accuracy=True, toolpath_safety=True)
    p3 = phase3_result(routing_timing_seconds=p3_timing, process_sla_seconds=3600.0)
    return framework_result(p1, p2, p3)


def test_framework_result_all_passed_when_p1_p2_pass_and_p3_pass():
    result = _make_full_result(p3_timing=500.0)
    assert result.all_passed is True
    assert result.phases_passed == 3


def test_framework_result_not_all_passed_when_p3_stub():
    result = _make_full_result(p3_timing=None)
    assert result.all_passed is False  # stub is not a PASS
    assert result.phases_passed == 2


def test_framework_result_as_dict_structure():
    result = _make_full_result(p3_timing=None)
    d = result.as_dict()

    assert set(d) >= {"phase1", "phase2", "phase3", "all_passed", "phases_passed", "private_weighted_score_available"}
    assert d["private_weighted_score_available"] is False
    assert d["phase3"]["status"] == "stub"


def test_framework_result_private_score_flag_is_false_by_default():
    result = _make_full_result()
    assert result.private_weighted_score_available is False


def test_phase_result_as_dict_has_submetric_results():
    p1 = phase1_result(geometric_integrity=True, constraint_fulfillment=False)
    d = p1.as_dict()
    assert d["status"] == "fail"
    assert d["submetric_results"]["geometric_integrity"] is True
    assert d["submetric_results"]["constraint_fulfillment"] is False
