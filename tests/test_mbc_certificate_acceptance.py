"""Issue #109 acceptance lock for `.mbc` MakerBench Certificates."""

from __future__ import annotations

import json
from pathlib import Path

from makerbench.certificate import (
    MBC_SIGNATURE_ALG,
    MbcCheckResult,
    MbcPayload,
    build_certificate,
    canonical_payload_bytes,
    verify_mbc,
    write_mbc,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "MBC_CERTIFICATE.md"
SCHEMA = ROOT / "schemas" / "mbc_certificate.schema.json"
KEY = "shared-nonce-key-v0.1"

REQUIRED_PAYLOAD_FIELDS = {
    "task_id",
    "seed",
    "score",
    "passed",
    "checks",
    "artifact_fingerprint",
    "artifact_hash_version",
    "video_evidence_sha256",
    "autonomy_ratio",
    "hii_highest_level",
    "toolchain_versions",
    "timestamp",
    "nonce",
}


def _payload(**overrides) -> MbcPayload:
    values = {
        "task_id": "vented_plate",
        "seed": 7,
        "score": 4,
        "passed": True,
        "checks": [
            MbcCheckResult(name="geometric", passed=True, detail="bbox ok"),
            MbcCheckResult(name="dfm", passed=True, detail="wall ok"),
        ],
        "artifact_fingerprint": "a" * 64,
        "artifact_hash_version": 2,
        "video_evidence_sha256": "b" * 64,
        "autonomy_ratio": 0.875,
        "hii_highest_level": "L1",
        "toolchain_versions": {"makerbench": "0.1.0", "openscad": "2021.01"},
        "timestamp": "2026-06-13T12:00:00Z",
        "nonce": "server-issued-nonce-123",
    }
    values.update(overrides)
    return MbcPayload(**values)


def test_story_109_payload_carries_grade_artifact_video_hii_toolchain_and_nonce():
    payload = _payload()
    data = payload.model_dump(mode="json")

    assert REQUIRED_PAYLOAD_FIELDS.issubset(data)
    assert data["score"] == 4
    assert data["video_evidence_sha256"] == "b" * 64
    assert data["autonomy_ratio"] == 0.875
    assert data["hii_highest_level"] == "L1"
    assert data["nonce"] == "server-issued-nonce-123"
    assert [check["name"] for check in data["checks"]] == ["geometric", "dfm"]


def test_story_109_signature_binds_canonical_payload_and_rejects_tampering(tmp_path):
    path = tmp_path / "run.mbc"
    text = write_mbc(_payload(), KEY, path=path)
    cert = build_certificate(_payload(), KEY)

    assert json.loads(text)["signature_alg"] == MBC_SIGNATURE_ALG
    assert canonical_payload_bytes(cert.payload) == canonical_payload_bytes(
        json.loads(text)["payload"]
    )
    assert verify_mbc(path, KEY, expected_nonce="server-issued-nonce-123") is True

    forged_score = text.replace('"score": 4', '"score": 3')
    forged_video = text.replace('"video_evidence_sha256": "' + "b" * 64, '"video_evidence_sha256": "' + "c" * 64)
    forged_hii = text.replace('"autonomy_ratio": 0.875', '"autonomy_ratio": 0.975')

    assert verify_mbc(forged_score, KEY) is False
    assert verify_mbc(forged_video, KEY) is False
    assert verify_mbc(forged_hii, KEY) is False


def test_story_109_nonce_and_key_checks_prevent_replay_or_wrong_authority():
    text = write_mbc(_payload(nonce="fresh-nonce"), KEY)

    assert verify_mbc(text, KEY, expected_nonce="fresh-nonce") is True
    assert verify_mbc(text, KEY, expected_nonce="stale-nonce") is False
    assert verify_mbc(text, "wrong-key", expected_nonce="fresh-nonce") is False


def test_story_109_docs_and_schema_expose_certificate_contract():
    doc = DOC.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    payload_props = schema["$defs"]["MbcPayload"]["properties"]

    for field in REQUIRED_PAYLOAD_FIELDS:
        assert field in payload_props

    for needle in (
        "tamper-evident local-grader payload",
        "video_evidence_sha256",
        "autonomy_ratio",
        "HMAC-SHA256",
        "nonce",
        "verify_mbc",
        "mutation of signed payload fields return `False`",
    ):
        assert needle in doc
