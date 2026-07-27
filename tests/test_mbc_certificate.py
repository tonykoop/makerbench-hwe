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


# --------------------------------------------------------------------------- #
# Forward-compat: the signed body evolves additively (#223)
# --------------------------------------------------------------------------- #
def test_payload_preserves_unknown_field_through_parse_and_dump():
    """A field a newer producer added survives parse + model_dump (extra=allow)."""
    raw = _payload().model_dump(mode="json")
    raw["fabrication_multiplier"] = 1.25  # field this version does not declare

    parsed = MbcPayload.model_validate(raw)
    assert parsed.model_dump(mode="json")["fabrication_multiplier"] == 1.25
    # the extra field rides into the canonical signing bytes, not dropped
    assert b"fabrication_multiplier" in canonical_payload_bytes(parsed)


def test_older_verifier_accepts_newer_producers_additive_field():
    """A newer producer signs a payload with an extra field; an older verify_mbc
    (this code) must still validate it instead of dropping the field and failing.
    """
    # Newer producer: build + sign a payload dict carrying an unknown field.
    payload_dict = _payload().model_dump(mode="json")
    payload_dict["fabrication_multiplier"] = 1.25
    signature = sign_payload(payload_dict, KEY)
    cert = {
        "payload": payload_dict,
        "signature_alg": MBC_SIGNATURE_ALG,
        "signature": signature,
    }

    # Older verifier re-serializes through the model; the field must be preserved
    # so the HMAC recomputes over the exact bytes the producer signed.
    assert verify_mbc(cert, KEY) is True
    assert verify_mbc(json.dumps(cert), KEY) is True


def test_forward_compat_still_detects_tampering_of_extra_field():
    """extra=allow must not weaken tamper-evidence: mutating the new field fails."""
    payload_dict = _payload().model_dump(mode="json")
    payload_dict["fabrication_multiplier"] = 1.25
    signature = sign_payload(payload_dict, KEY)

    payload_dict["fabrication_multiplier"] = 2.0  # tamper after signing
    cert = {
        "payload": payload_dict,
        "signature_alg": MBC_SIGNATURE_ALG,
        "signature": signature,
    }
    assert verify_mbc(cert, KEY) is False
