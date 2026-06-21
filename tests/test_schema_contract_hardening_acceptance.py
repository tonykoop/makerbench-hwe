"""Acceptance locks for schema/contract hardening (#223)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from makerbench.certificate import (
    MBC_SIGNATURE_ALG,
    MbcCheckResult,
    MbcPayload,
    sign_payload,
    verify_mbc,
)
from makerbench.schema import HumanInterventionIndex, RunResults


KEY = "schema-contract-hardening-key-v0.1"


def _certificate_payload() -> dict:
    return MbcPayload(
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
    ).model_dump(mode="json")


def test_hii_rejects_hand_authored_summary_that_disagrees_with_counts():
    with pytest.raises(ValidationError, match="autonomy_ratio must match HII event counts"):
        HumanInterventionIndex(
            l0_autonomous_events=10,
            l1_nl_steering_events=0,
            l2_copilot_manual_events=2,
            autonomy_ratio=1.0,
            highest_level="L2",
        )

    with pytest.raises(ValidationError, match="highest_level must match HII event counts"):
        HumanInterventionIndex(
            l0_autonomous_events=10,
            l1_nl_steering_events=0,
            l2_copilot_manual_events=2,
            autonomy_ratio=round(10 / 12, 6),
            highest_level="L0",
        )


def test_run_results_schema_version_is_additive_and_serialized():
    legacy_payload = {
        "benchmark_version": "0.1.0",
        "model_identifier": "legacy-model",
        "results": [],
    }

    parsed = RunResults.model_validate(legacy_payload)
    assert parsed.schema_version == "0.1"
    assert parsed.model_dump(mode="json")["schema_version"] == "0.1"

    fresh = RunResults(benchmark_version="0.1.0", model_identifier="model", results=[])
    assert json.loads(fresh.model_dump_json())["schema_version"] == "0.1"


def test_mbc_forward_compat_extra_fields_remain_signed_and_verified():
    payload = _certificate_payload()
    payload["fabrication_multiplier"] = 1.25
    cert = {
        "payload": payload,
        "signature_alg": MBC_SIGNATURE_ALG,
        "signature": sign_payload(payload, KEY),
    }

    assert verify_mbc(cert, KEY) is True
    assert verify_mbc(json.dumps(cert), KEY) is True

    cert["payload"]["fabrication_multiplier"] = 2.0
    assert verify_mbc(cert, KEY) is False
