"""The `.mbc` MakerBench Certificate — a signed local-grader payload (mb#109).

A workflow-track run is graded headlessly on the contributor's machine. The score
that comes back must be tamper-evident before a public surface (the leaderboard,
an HII badge, the Delta-Dossier history) trusts it. The `.mbc` certificate is that
substrate: the local grader bundles the geometry verdict, the artifact fingerprint,
the video-evidence hash, the HII/autonomy ratio, the toolchain versions, and a
timestamp into one payload and signs it, so the bytes cannot be hand-edited after
grading without the signature failing.

v0.1 signing is an HMAC-SHA256 over a server-issued nonce: it proves the payload
was produced by a holder of the shared key and has not been mutated since. It is a
lightweight anti-gaming primitive — it does not require trusting the *process*,
only that the signed numbers match what the grader emitted. A real deployment can
swap the HMAC for a detached asymmetric signature without changing the envelope.

This module is additive and self-contained; it does not import or alter any
existing grading path. The canonical-JSON convention (sorted keys, tight
separators, ASCII) matches ``attestation.normalized_result_payload`` so digests
are stable across machines.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

MBC_KIND = "makerbench_certificate"
MBC_SCHEMA_VERSION = "0.1"
MBC_SIGNATURE_ALG = "HMAC-SHA256"


class MbcCheckResult(BaseModel):
    """One graded check carried inside the certificate verdict."""

    name: str
    passed: bool
    detail: str = ""


class MbcPayload(BaseModel):
    """The signed body of a `.mbc` certificate.

    Everything a public surface needs to trust a local grade without re-running it:
    the verdict + per-check results, the artifact fingerprint it was computed over,
    the video-evidence hash, the HII/autonomy ratio, the toolchain that produced it,
    a timestamp, and the server-issued ``nonce`` the signature is bound to.
    """

    schema_version: str = MBC_SCHEMA_VERSION
    kind: str = MBC_KIND
    task_id: str
    seed: int
    # Graded outcome.
    score: int = Field(description="Geometry score (highest contiguous L1-L4 level passed).")
    passed: bool = Field(description="True if the run met the pass bar for the task.")
    checks: list[MbcCheckResult] = Field(default_factory=list)
    # What the verdict was computed over.
    artifact_fingerprint: str = Field(
        description="Canonical artifact hash (e.g. GradeResult.artifact_sha256)."
    )
    artifact_hash_version: Optional[int] = Field(
        default=None, description="Fingerprint contract version for artifact_fingerprint."
    )
    video_evidence_sha256: Optional[str] = Field(
        default=None, description="Hash of the session video/recording evidence, when present."
    )
    # Disclosed process signals (carried from the WorkflowManifest).
    autonomy_ratio: Optional[float] = Field(
        default=None, description="HII autonomy ratio (0..1) for the run."
    )
    hii_highest_level: Optional[str] = Field(
        default=None, description="Heaviest HII tier observed (L0|L1|L2)."
    )
    # Reproducibility + freshness.
    toolchain_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Grader/stack versions (e.g. from provenance.grader_environment()).",
    )
    timestamp: str = Field(description="ISO-8601 UTC timestamp of grading.")
    nonce: str = Field(
        description="Server-issued nonce the signature is bound to (anti-replay)."
    )


class MbcCertificate(BaseModel):
    """The signed envelope written to a `.mbc` file: payload + detached signature."""

    payload: MbcPayload
    signature_alg: str = MBC_SIGNATURE_ALG
    signature: str = Field(description="Hex HMAC of the canonical payload bytes.")


def canonical_payload_bytes(payload: Union[MbcPayload, dict[str, Any]]) -> bytes:
    """Deterministic bytes for signing/verifying a payload.

    Sorted keys, tight separators, ASCII — identical convention to
    ``attestation.normalized_result_payload`` so two machines produce the same
    digest for the same payload.
    """
    data = payload.model_dump(mode="json") if isinstance(payload, MbcPayload) else payload
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sign_payload(payload: Union[MbcPayload, dict[str, Any]], key: Union[str, bytes]) -> str:
    """Return the hex HMAC-SHA256 of the canonical payload bytes under ``key``."""
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    return hmac.new(key_bytes, canonical_payload_bytes(payload), hashlib.sha256).hexdigest()


def build_certificate(payload: MbcPayload, key: Union[str, bytes]) -> MbcCertificate:
    """Sign ``payload`` and wrap it in a certificate envelope."""
    return MbcCertificate(payload=payload, signature=sign_payload(payload, key))


def write_mbc(
    payload: MbcPayload,
    key: Union[str, bytes],
    *,
    path: Union[str, Path, None] = None,
) -> str:
    """Build a signed certificate and (optionally) write it to ``path``.

    Returns the certificate's JSON text. When ``path`` is given the same text is
    written there (conventionally a ``.mbc`` file).
    """
    cert = build_certificate(payload, key)
    text = json.dumps(cert.model_dump(mode="json"), indent=2, sort_keys=True)
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


def verify_mbc(
    source: Union[str, bytes, Path, MbcCertificate, dict[str, Any]],
    key: Union[str, bytes],
    *,
    expected_nonce: Optional[str] = None,
) -> bool:
    """Verify a `.mbc` certificate against ``key``.

    Returns True only if the certificate parses, its signature recomputes exactly
    over the canonical payload bytes (constant-time compare), and — when
    ``expected_nonce`` is supplied — the payload nonce matches it. Any byte mutated
    in the payload changes the canonical bytes and fails the HMAC check, so a
    hand-edited score/ratio/fingerprint is rejected. Malformed input returns False
    rather than raising.
    """
    try:
        cert = _coerce_certificate(source)
    except (ValueError, TypeError, json.JSONDecodeError, OSError):
        return False
    if cert.signature_alg != MBC_SIGNATURE_ALG:
        return False
    if expected_nonce is not None and cert.payload.nonce != expected_nonce:
        return False
    expected_sig = sign_payload(cert.payload, key)
    return hmac.compare_digest(expected_sig, cert.signature)


def _coerce_certificate(
    source: Union[str, bytes, Path, MbcCertificate, dict[str, Any]],
) -> MbcCertificate:
    if isinstance(source, MbcCertificate):
        return source
    if isinstance(source, dict):
        return MbcCertificate.model_validate(source)
    if isinstance(source, Path):
        return MbcCertificate.model_validate_json(source.read_text(encoding="utf-8"))
    if isinstance(source, bytes):
        return MbcCertificate.model_validate_json(source.decode("utf-8"))
    # str: a filesystem path if it points at a real file, else raw JSON text.
    if isinstance(source, str):
        candidate = Path(source)
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        if is_file:
            return MbcCertificate.model_validate_json(candidate.read_text(encoding="utf-8"))
        return MbcCertificate.model_validate_json(source)
    raise TypeError(f"unsupported certificate source: {type(source)!r}")
