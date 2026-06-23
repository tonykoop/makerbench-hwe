"""Issue-level acceptance lock for the code-CAD instrument loop (#83)."""

from __future__ import annotations

import json
from pathlib import Path

from makerbench.instrument_acoustics_ladder import scale_length_check
from makerbench.schema import RunResults, WorkflowManifest
from makerbench_core import score_file

from examples.closed_loop_instrument_funnel_demo import build_combined_result


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "CLOSED_LOOP_INSTRUMENT_DEMO.md"
ACOUSTIC_RESULT = ROOT / "examples" / "closed_loop_instrument_demo.results.json"
WORKFLOW_MANIFEST = ROOT / "examples" / "closed_loop_instrument_demo.workflow_manifest.json"
FUNNEL_RESULT = ROOT / "examples" / "closed_loop_instrument_funnel_demo.results.json"
STEP = ROOT / "examples" / "closed_loop_instrument_bridge.step"


def test_doc_describes_the_three_phase_code_cad_to_makerbench_loop():
    doc = DOC.read_text(encoding="utf-8")

    for phrase in (
        "Generative math",
        "CAD copilot cleanup",
        "Export",
        "MakerBench gate",
        "lyre/kora bridge layout board",
        "Fable 5 plus",
        "Adam CAD",
        "Fusion or Onshape",
    ):
        assert phrase in doc


def test_pilot_result_recomputes_the_public_acoustic_gate():
    payload = RunResults.model_validate_json(ACOUSTIC_RESULT.read_text(encoding="utf-8"))
    result = payload.results[0]
    quality = result.grade.quality

    assert payload.benchmark_profile == "workflow-instrument-acoustics-demo"
    assert payload.harness_class == "assisted-workflow"
    assert payload.harness_subclass == "gui-injected-copilot"
    assert result.task_id == "acoustics_scale_length"
    assert result.grade.score == 4

    recomputed = scale_length_check({
        "declared_scale_mm": quality["measured_scale_mm"],
        "target_scale_mm": quality["target_scale_mm"],
        "scale_tolerance_mm": quality["scale_tolerance_mm"],
        "nut_to_bridge_mm": quality["measured_nut_to_bridge_mm"],
        "saddle_intonation_mm": quality["saddle_intonation_mm"],
    })
    assert recomputed["feasible"] == 1.0
    assert recomputed["scale_error_mm"] == quality["scale_error_mm"]


def test_workflow_manifest_discloses_the_copilot_stack_without_reproducing_it():
    manifest = WorkflowManifest.model_validate_json(WORKFLOW_MANIFEST.read_text(encoding="utf-8"))
    result_payload = json.loads(ACOUSTIC_RESULT.read_text(encoding="utf-8"))

    assert manifest.task_id == "acoustics_scale_length"
    assert manifest.stack.orchestrator.name == "Fable"
    assert manifest.stack.host_application.name == "Autodesk Fusion or Onshape"
    assert manifest.stack.execution_bridge.name == "Adam CAD code-CAD copilot"
    assert manifest.hii.highest_level == "L1"
    assert manifest.dossier is not None
    assert "proprietary CAD source" in manifest.dossier.assumptions[0]
    assert (
        result_payload["runner_environment"]["source_artifact_policy"]
        == "metadata-only; CAD/STEP/source artifacts are not committed publicly"
    )


def test_phase_3b_routes_the_same_pilot_through_dfm_and_cost_gates():
    captured = json.loads(FUNNEL_RESULT.read_text(encoding="utf-8"))
    recomputed = build_combined_result()

    assert captured == recomputed
    assert recomputed["pilot"] == "lyre-kora-bridge"
    assert recomputed["both_gates_passed"] is True
    assert recomputed["acoustics"]["feasible"] == 1.0
    assert recomputed["dfm"] == {
        "profile": "portable-dfm-v1",
        "score": 100.0,
        "passed": True,
    }
    assert recomputed["manufacturing"]["quote_vendor"] == "stub"
    assert recomputed["manufacturing"]["purchase_order_allowed"] is False

    dfm = score_file(STEP)
    assert dfm.passed is True
    assert dfm.makerbench_dfm_score == 100.0
    assert dfm.input["format"] == "step"


def test_demo_surfaces_benchmark_gaps_and_instrument_repo_homes():
    doc = DOC.read_text(encoding="utf-8")

    for issue in (
        "https://github.com/tonykoop/makerbench-hwe/issues/129",
        "https://github.com/tonykoop/makerbench-hwe/issues/131",
    ):
        assert issue in doc

    for repo in ("flutes", "djembe", "fujara", "didgeridoo", "conga", "dundun", "lyre", "kora"):
        assert f"https://github.com/tonykoop/{repo}" in doc

    for gate in ("resonator_volume_check", "bore_resonance_check", "scale_length_check"):
        assert gate in doc
