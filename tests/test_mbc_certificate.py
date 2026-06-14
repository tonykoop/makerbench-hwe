"""Standalone `.mbc` MakerBench Certificate contract tests (mb#109)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from makerbench.certificate import (
    MBC_KIND,
    MBC_SCHEMA_VERSION,
    MBC_SIGNATURE_ALG,
    MbcCheckResult,
    MbcCertificate,
    MbcPayload,
    build_certificate,
    canonical_payload_bytes,
    sign_payload,
    verify_mbc,
    write_mbc,
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


def test_certificate_envelope_uses_schema_constants():
    cert = build_certificate(_payload(), KEY)

    assert cert.payload.schema_version == MBC_SCHEMA_VERSION
    assert cert.payload.kind == MBC_KIND
    assert cert.signature_alg == MBC_SIGNATURE_ALG

    schema = MbcCertificate.model_json_schema()
    payload_schema = schema["$defs"]["MbcPayload"]["properties"]
    assert payload_schema["schema_version"]["const"] == MBC_SCHEMA_VERSION
    assert payload_schema["kind"]["const"] == MBC_KIND
    assert schema["properties"]["signature_alg"]["const"] == MBC_SIGNATURE_ALG


def test_write_mbc_round_trips_json_file(tmp_path):
    path = tmp_path / "run.mbc"
    text = write_mbc(_payload(), KEY, path=path)

    assert json.loads(text)["signature_alg"] == MBC_SIGNATURE_ALG
    assert path.read_text(encoding="utf-8") == text
    assert verify_mbc(path, KEY, expected_nonce="server-issued-nonce-123")


def test_signature_is_hmac_over_canonical_score_payload():
    payload = _payload()
    cert = build_certificate(payload, KEY)

    assert cert.signature == sign_payload(payload, KEY)
    raw = canonical_payload_bytes(payload)
    assert raw == canonical_payload_bytes(json.loads(raw))
    assert b", " not in raw
    assert json.loads(raw)["score"] == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", 5),
        ("autonomy_ratio", 1.01),
        ("hii_highest_level", "L9"),
    ],
)
def test_payload_rejects_values_outside_certificate_schema(field, value):
    with pytest.raises(ValidationError):
        _payload(**{field: value})


def test_verify_rejects_payload_tampering_even_when_json_remains_valid():
    text = write_mbc(_payload(score=2), KEY)
    forged = text.replace('"score": 2', '"score": 4')

    assert forged != text
    assert verify_mbc(forged, KEY) is False


def test_verify_rejects_algorithm_confusion():
    cert = build_certificate(_payload(), KEY).model_dump(mode="json")
    cert["signature_alg"] = "none"

    assert verify_mbc(cert, KEY) is False
