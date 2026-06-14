"""WorkflowManifest + Human Intervention Index + .mbc certificate contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from makerbench.certificate import (
    MBC_SIGNATURE_ALG,
    MbcCheckResult,
    MbcPayload,
    build_certificate,
    canonical_payload_bytes,
    sign_payload,
    verify_mbc,
    write_mbc,
)
from makerbench.schema import (
    ComponentVersion,
    DesignDossier,
    HumanInterventionIndex,
    ProvenanceTrace,
    StackDescriptor,
    WorkflowManifest,
    WorkflowMetrics,
)

KEY = "shared-nonce-key-v0.1"


def _payload(**overrides) -> MbcPayload:
    base = dict(
        task_id="vented_plate",
        seed=0,
        score=3,
        passed=True,
        checks=[MbcCheckResult(name="structural", passed=True)],
        artifact_fingerprint="a" * 64,
        artifact_hash_version=2,
        video_evidence_sha256="b" * 64,
        autonomy_ratio=0.79,
        hii_highest_level="L1",
        toolchain_versions={"openscad": "2021.01", "makerbench": "0.1.0"},
        timestamp="2026-06-13T12:00:00Z",
        nonce="server-issued-nonce-123",
    )
    base.update(overrides)
    return MbcPayload(**base)


# --- WorkflowManifest -------------------------------------------------------


def test_manifest_defaults_are_present_and_additive():
    m = WorkflowManifest(task_id="vented_plate", seed=0)
    assert m.schema_version == "0.1"
    # Every nested contract has a default so a sparse manifest still validates.
    assert isinstance(m.stack, StackDescriptor)
    assert isinstance(m.metrics, WorkflowMetrics)
    assert isinstance(m.hii, HumanInterventionIndex)
    assert isinstance(m.provenance_trace, ProvenanceTrace)
    assert m.dossier is None


def test_manifest_round_trips_through_json():
    m = WorkflowManifest(
        task_id="vented_plate",
        seed=7,
        stack=StackDescriptor(
            orchestrator=ComponentVersion(name="claude-code", version="0.1.0"),
            host_application=ComponentVersion(name="blender", version="4.2"),
            execution_bridge=ComponentVersion(name="blender-mcp"),
        ),
        metrics=WorkflowMetrics(wall_clock_seconds=42.0, tool_calls_count=9),
        hii=HumanInterventionIndex.from_events(l0=8, l1=3, l2=1),
        provenance_trace=ProvenanceTrace(session_recording_hash="c" * 64),
        dossier=DesignDossier(task_id="vented_plate", seed=7, fabrication_domain="enclosure"),
    )
    restored = WorkflowManifest.model_validate_json(m.model_dump_json())
    assert restored == m
    assert restored.stack.execution_bridge.version is None


def test_manifest_accepts_plain_string_stack_components():
    m = WorkflowManifest.model_validate({
        "task_id": "vented_plate",
        "seed": 0,
        "stack": {
            "framework": "makerbench-logger",
            "execution_bridge": "blender-mcp",
        },
    })

    assert m.stack.framework == ComponentVersion(name="makerbench-logger")
    assert m.stack.execution_bridge == ComponentVersion(name="blender-mcp")


# --- Human Intervention Index ----------------------------------------------


def test_hii_fully_autonomous_is_ratio_one():
    hii = HumanInterventionIndex.from_events(l0=10)
    assert hii.autonomy_ratio == 1.0
    assert hii.highest_level == "L0"


def test_hii_no_events_defaults_to_autonomous():
    hii = HumanInterventionIndex.from_events()
    assert hii.autonomy_ratio == 1.0
    assert hii.highest_level == "L0"


def test_hii_ratio_decreases_with_heavier_steering():
    light = HumanInterventionIndex.from_events(l0=8, l1=2)
    heavy = HumanInterventionIndex.from_events(l0=8, l2=2)
    assert light.autonomy_ratio > heavy.autonomy_ratio
    assert heavy.highest_level == "L2"
    # L1 costs half autonomy, L2 costs all of it: (8*1 + 2*0.5)/10 = 0.9; (8*1)/10 = 0.8.
    assert light.autonomy_ratio == 0.9
    assert heavy.autonomy_ratio == 0.8


def test_hii_highest_level_prefers_heaviest_tier():
    assert HumanInterventionIndex.from_events(l0=1, l1=1).highest_level == "L1"
    assert HumanInterventionIndex.from_events(l0=1, l1=1, l2=1).highest_level == "L2"


# --- .mbc certificate -------------------------------------------------------


def test_mbc_write_and_verify_round_trip(tmp_path):
    path = tmp_path / "out.mbc"
    text = write_mbc(_payload(), KEY, path=path)
    assert path.read_text(encoding="utf-8") == text
    assert verify_mbc(text, KEY) is True
    assert verify_mbc(path, KEY) is True
    assert verify_mbc(str(path), KEY) is True
    assert verify_mbc(text.encode("utf-8"), KEY) is True


def test_mbc_signature_uses_declared_alg():
    cert = build_certificate(_payload(), KEY)
    assert cert.signature_alg == MBC_SIGNATURE_ALG
    assert cert.signature == sign_payload(cert.payload, KEY)


def test_mbc_verify_fails_when_a_byte_is_mutated():
    text = write_mbc(_payload(), KEY)
    # Flip the graded score in the serialized payload — signature must reject it.
    mutated = text.replace('"score": 3', '"score": 4')
    assert mutated != text
    assert verify_mbc(mutated, KEY) is False


def test_mbc_verify_fails_on_wrong_key():
    text = write_mbc(_payload(), KEY)
    assert verify_mbc(text, "attacker-key") is False


def test_mbc_verify_fails_on_tampered_signature():
    cert = build_certificate(_payload(), KEY)
    forged = cert.model_dump(mode="json")
    forged["signature"] = "00" * 32
    assert verify_mbc(forged, KEY) is False


def test_mbc_verify_checks_expected_nonce():
    text = write_mbc(_payload(nonce="real-nonce"), KEY)
    assert verify_mbc(text, KEY, expected_nonce="real-nonce") is True
    assert verify_mbc(text, KEY, expected_nonce="stale-nonce") is False


def test_mbc_verify_rejects_malformed_input():
    assert verify_mbc("not json at all", KEY) is False
    assert verify_mbc("{}", KEY) is False
    assert verify_mbc(b"\xff\xfe", KEY) is False


# --- exported JSON Schema ---------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_exported_schemas_are_not_stale():
    """The committed schemas/ files must match the current pydantic models."""
    proc = subprocess.run(
        [sys.executable, "scripts/export_workflow_schemas.py", "--check"],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_exported_manifest_schema_is_nonempty_json_schema():
    schema = json.loads((REPO_ROOT / "schemas" / "workflow_manifest.schema.json").read_text())
    assert schema.get("title") == "WorkflowManifest"
    assert "stack" in schema["properties"]
    assert "hii" in schema["properties"]


def test_committed_example_manifest_validates():
    example = json.loads(
        (REPO_ROOT / "schemas" / "examples" / "workflow_manifest.example.json").read_text()
    )
    manifest = WorkflowManifest.model_validate(example)
    assert manifest.task_id == "vented_plate"
    assert 0.0 <= manifest.hii.autonomy_ratio <= 1.0


def test_canonical_payload_bytes_are_deterministic():
    p = _payload()
    assert canonical_payload_bytes(p) == canonical_payload_bytes(p.model_dump(mode="json"))
    # Sorted keys + tight separators, like attestation.normalized_result_payload.
    assert b", " not in canonical_payload_bytes(p)
    assert json.loads(canonical_payload_bytes(p))["score"] == 3
