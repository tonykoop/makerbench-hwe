"""End-to-end deflationary funnel composition (issue #82, epic #240).

Gate 1 (DFM) -> Gate 2 (local cost) -> Gate 3 (vendor quote stub) -> Gate 4
(human approval request). The funnel must be deterministic, offline, and must
never authorize a purchase order.
"""

from __future__ import annotations

import json

from makerbench.costing import GeometryCostMetrics, ProcessRateProfile
from makerbench.funnel import run_funnel

MINIMAL_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('MakerBench funnel test STEP'), '2;1');
FILE_NAME('part.step', '2026-06-14T00:00:00', (), (), 'makerbench', '', '');
FILE_SCHEMA(('AP214'));
ENDSEC;
DATA;
#1 = PRODUCT('bracket', 'bracket', '', (#2));
#2 = PRODUCT_CONTEXT('', #3, '');
#3 = APPLICATION_CONTEXT('mechanical design');
#4 = CARTESIAN_POINT('', (0.0, 0.0, 0.0));
#5 = PRODUCT_DEFINITION('design', '', #6, #7);
#6 = PRODUCT_DEFINITION_FORMATION('', '', #1);
#7 = PRODUCT_DEFINITION_CONTEXT('', #3, 'design');
ENDSEC;
END-ISO-10303-21;
"""


def _cnc_inputs():
    metrics = GeometryCostMetrics(
        material_volume_mm3=12_000,
        removed_volume_mm3=3_000,
        hole_count=4,
    )
    profile = ProcessRateProfile(
        process_id="cnc_milling",
        profile_id="funnel-cnc-v1",
        material_id="6061-aluminum",
        material_usd_per_cm3=0.2,
        machine_usd_per_hour=60.0,
        removal_rate_cm3_per_min=1.5,
        setup_fee_usd=10.0,
        hole_usd_each=0.25,
        min_job_fee_usd=25.0,
    )
    return metrics, profile


def test_full_funnel_runs_all_four_gates(tmp_path):
    artifact = tmp_path / "part.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")
    metrics, profile = _cnc_inputs()

    result = run_funnel(
        artifact,
        quote_process="cnc_machining",
        material="aluminum-6061",
        cost_metrics=metrics,
        cost_profile=profile,
    )

    assert result.gates_passed == ["dfm", "local_cost", "quote", "approval_request"]
    assert result.blocked_at is None
    assert result.dfm_passed is True
    assert result.cost_estimate is not None and result.cost_estimate.total_usd > 0
    assert result.quote is not None and result.quote.status == "quoted"
    # The local estimate is attached to the vendor quote for comparison.
    assert result.quote.local_estimate_usd == result.cost_estimate.total_usd
    assert result.approval_request is not None
    assert result.approval_request.human_approval_required is True


def test_funnel_never_authorizes_a_purchase_order(tmp_path):
    artifact = tmp_path / "part.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")
    metrics, profile = _cnc_inputs()

    result = run_funnel(
        artifact,
        quote_process="cnc_machining",
        material="aluminum-6061",
        cost_metrics=metrics,
        cost_profile=profile,
    )

    assert result.purchase_order_allowed is False
    assert result.quote.purchase_order_allowed is False
    assert result.approval_request.may_place_order is False


def test_funnel_is_deterministic(tmp_path):
    artifact = tmp_path / "part.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")
    metrics, profile = _cnc_inputs()

    first = run_funnel(
        artifact, quote_process="cnc_machining", material="al", cost_metrics=metrics, cost_profile=profile
    )
    second = run_funnel(
        artifact, quote_process="cnc_machining", material="al", cost_metrics=metrics, cost_profile=profile
    )
    assert first.to_dict() == second.to_dict()


def test_funnel_blocks_at_dfm_for_invalid_geometry(tmp_path):
    artifact = tmp_path / "part.step"
    artifact.write_text("not really a STEP file", encoding="utf-8")

    result = run_funnel(artifact, quote_process="cnc_machining", material="al")

    assert result.blocked_at == "dfm"
    assert result.gates_passed == []
    assert result.quote is None
    assert result.dfm_passed is False


def test_funnel_skips_quote_for_non_step_geometry(tmp_path):
    artifact = tmp_path / "part.scad"
    artifact.write_text("cube([10, 10, 2]);\n", encoding="utf-8")

    result = run_funnel(artifact, quote_process="cnc_machining", material="al")

    assert "dfm" in result.gates_passed
    assert result.blocked_at == "quote"
    assert result.quote is None
    assert any("STEP only" in n for n in result.notes)


def test_funnel_quotes_without_local_cost_gate(tmp_path):
    artifact = tmp_path / "part.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")

    result = run_funnel(artifact, quote_process="cnc_machining", material="al")

    assert result.gates_passed == ["dfm", "quote", "approval_request"]
    assert result.cost_estimate is None
    assert result.quote is not None


def test_funnel_result_is_json_serializable(tmp_path):
    artifact = tmp_path / "part.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")
    metrics, profile = _cnc_inputs()

    result = run_funnel(
        artifact,
        quote_process="cnc_machining",
        material="aluminum-6061",
        cost_metrics=metrics,
        cost_profile=profile,
    )
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["purchase_order_allowed"] is False
    assert payload["quote"]["status"] == "quoted"
