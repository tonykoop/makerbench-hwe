"""Issue-level acceptance tests for WorkflowManifest + HII (#89)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from makerbench.schema import (
    ComponentVersion,
    DesignDossier,
    HumanInterventionIndex,
    ProvenanceTrace,
    StackDescriptor,
    WorkflowManifest,
    WorkflowMetrics,
)


ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> WorkflowManifest:
    return WorkflowManifest(
        task_id="vented_plate",
        seed=0,
        stack=StackDescriptor(
            orchestrator=ComponentVersion(name="codex", version="5.0"),
            framework=ComponentVersion(name="makerbench-logger", version="0.1.0"),
            host_application=ComponentVersion(name="blender", version="4.2"),
            execution_bridge=ComponentVersion(name="blender-mcp", version="0.3.0"),
        ),
        metrics=WorkflowMetrics(
            wall_clock_seconds=412.5,
            inference_tokens=184320,
            tool_calls_count=37,
            estimated_cost_usd=1.84,
        ),
        hii=HumanInterventionIndex.from_events(l0=11, l1=3, l2=1),
        provenance_trace=ProvenanceTrace(
            tool_call_log_url="https://example.test/runs/vented_plate/trace.json",
            session_recording_hash="sha256:" + "c" * 64,
        ),
        dossier=DesignDossier(
            task_id="vented_plate",
            seed=0,
            fabrication_domain="enclosure",
        ),
    )


def test_workflow_manifest_schema_carries_issue_89_required_sections():
    manifest = _manifest()

    assert manifest.schema_version == "0.1"
    assert manifest.stack.orchestrator == ComponentVersion(name="codex", version="5.0")
    assert manifest.stack.framework == ComponentVersion(
        name="makerbench-logger",
        version="0.1.0",
    )
    assert manifest.stack.host_application == ComponentVersion(name="blender", version="4.2")
    assert manifest.stack.execution_bridge == ComponentVersion(
        name="blender-mcp",
        version="0.3.0",
    )
    assert manifest.metrics.wall_clock_seconds == 412.5
    assert manifest.metrics.inference_tokens == 184320
    assert manifest.metrics.tool_calls_count == 37
    assert manifest.metrics.estimated_cost_usd == 1.84
    assert manifest.hii.highest_level == "L2"
    assert manifest.hii.autonomy_ratio == 0.833333
    assert manifest.provenance_trace.tool_call_log_url.endswith("trace.json")
    assert manifest.provenance_trace.session_recording_hash.startswith("sha256:")


def test_workflow_manifest_extends_dossier_and_perception_trace_by_composition():
    restored = WorkflowManifest.model_validate_json(_manifest().model_dump_json())

    assert restored.dossier is not None
    assert restored.dossier.task_id == restored.task_id
    assert restored.dossier.seed == restored.seed
    assert restored.provenance_trace.tool_call_log_url is not None
    docs = (ROOT / "docs" / "WORKFLOW_TRACK_MANIFEST.md").read_text(encoding="utf-8")
    assert "DesignDossier" in docs
    assert "perception_trace" in docs


def test_sparse_manifest_defaults_are_additive_for_legacy_workflows():
    manifest = WorkflowManifest(task_id="vented_plate", seed=0)

    assert isinstance(manifest.stack, StackDescriptor)
    assert isinstance(manifest.metrics, WorkflowMetrics)
    assert isinstance(manifest.hii, HumanInterventionIndex)
    assert isinstance(manifest.provenance_trace, ProvenanceTrace)
    assert manifest.hii.highest_level == "L0"
    assert manifest.hii.autonomy_ratio == 1.0
    assert manifest.dossier is None


def test_hii_levels_and_derived_values_match_the_human_intervention_index():
    fully_autonomous = HumanInterventionIndex.from_events(l0=8)
    light_steering = HumanInterventionIndex.from_events(l0=8, l1=2)
    manual_geometry = HumanInterventionIndex.from_events(l0=8, l1=1, l2=1)

    assert fully_autonomous.highest_level == "L0"
    assert fully_autonomous.autonomy_ratio == 1.0
    assert light_steering.highest_level == "L1"
    assert light_steering.autonomy_ratio == 0.9
    assert manual_geometry.highest_level == "L2"
    assert manual_geometry.autonomy_ratio == 0.85


def test_hii_validator_rejects_hand_edited_gaming_values():
    with pytest.raises(ValidationError, match="autonomy_ratio must match HII event counts"):
        WorkflowManifest.model_validate({
            "task_id": "vented_plate",
            "seed": 0,
            "hii": {
                "l0_autonomous_events": 0,
                "l1_nl_steering_events": 0,
                "l2_copilot_manual_events": 4,
                "autonomy_ratio": 1.0,
                "highest_level": "L2",
            },
        })

    with pytest.raises(ValidationError, match="highest_level must match HII event counts"):
        HumanInterventionIndex(
            l0_autonomous_events=2,
            l1_nl_steering_events=0,
            l2_copilot_manual_events=1,
            autonomy_ratio=0.666667,
            highest_level="L1",
        )


def test_plain_string_stack_components_normalize_for_logger_compatibility():
    manifest = WorkflowManifest.model_validate({
        "task_id": "vented_plate",
        "seed": 0,
        "stack": {
            "orchestrator": "codex",
            "framework": "makerbench-logger",
            "host_application": "blender",
            "execution_bridge": "blender-mcp",
        },
    })

    assert manifest.stack.orchestrator == ComponentVersion(name="codex")
    assert manifest.stack.framework == ComponentVersion(name="makerbench-logger")
    assert manifest.stack.host_application == ComponentVersion(name="blender")
    assert manifest.stack.execution_bridge == ComponentVersion(name="blender-mcp")


def test_exported_schema_and_example_manifest_cover_the_contract():
    schema = json.loads((ROOT / "schemas" / "workflow_manifest.schema.json").read_text())
    example = json.loads(
        (ROOT / "schemas" / "examples" / "workflow_manifest.example.json").read_text()
    )

    assert schema["title"] == "WorkflowManifest"
    assert {"task_id", "seed", "stack", "metrics", "hii", "provenance_trace"} <= set(
        schema["properties"]
    )
    assert {"tool_call_log_url", "session_recording_hash"} <= set(
        schema["$defs"]["ProvenanceTrace"]["properties"]
    )
    assert {"l0_autonomous_events", "l1_nl_steering_events", "l2_copilot_manual_events"} <= set(
        schema["$defs"]["HumanInterventionIndex"]["properties"]
    )

    manifest = WorkflowManifest.model_validate(example)
    assert manifest.stack.execution_bridge.name == "blender-mcp"
    assert manifest.dossier is not None
    assert manifest.dossier.task_id == manifest.task_id


def test_committed_workflow_schema_export_is_current():
    proc = subprocess.run(
        [sys.executable, "scripts/export_workflow_schemas.py", "--check"],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr


def test_docs_pin_artifact_hard_graded_process_disclosed_boundary():
    docs = (ROOT / "docs" / "WORKFLOW_TRACK_MANIFEST.md").read_text(encoding="utf-8")
    normalized = " ".join(docs.split())

    assert "The artifact is hard-graded; the process is disclosed, never a score input." in (
        normalized
    )
    assert "DesignDossier" in docs
    assert "perception_trace" in docs
    assert "tool_call_log_url" in docs
    assert "session_recording_hash" in docs
    for level in ("L0", "L1", "L2"):
        assert level in docs
