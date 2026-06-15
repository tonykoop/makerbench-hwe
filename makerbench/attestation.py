"""Public verification of private submission regrade attestations.

True zero-public-exposure submissions never commit source artifacts to the public
PR. A maintainer workflow regrades them from the private submissions archive and
posts a trusted attestation comment on the PR. Public CI validates that comment
against the metadata-only result JSON.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .regrade import RegradeReport

ATTESTATION_KIND = "makerbench_private_regrade_attestation"
ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_MARKER = "<!-- makerbench-private-regrade-attestation:v1 -->"

TRUSTED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
TRUSTED_BOT_LOGINS = {"github-actions[bot]"}


def normalized_result_payload(path: Path | str) -> bytes:
    """Canonical result payload bytes for attestation.

    Maintainers may set ``verification_status`` after the private regrade, so the
    status field is normalized away. The attestation binds the substantive score,
    dossier, artifact path, and artifact hash data.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["verification_status"] = "unverified"
    payload.pop("signature", None)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def result_payload_sha256(path: Path | str) -> str:
    return hashlib.sha256(normalized_result_payload(path)).hexdigest()


def source_artifacts_for_result(path: Path | str) -> list[dict[str, str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    artifacts: list[dict[str, str]] = []
    for idx, row in enumerate(payload.get("results") or []):
        dossier = row.get("dossier") or {}
        for artifact in dossier.get("artifacts") or []:
            if artifact.get("role") != "source":
                continue
            artifacts.append(
                {
                    "row_index": idx,
                    "path": str(artifact.get("path") or ""),
                    "format": str(artifact.get("format") or ""),
                    "sha256": str(artifact.get("sha256") or ""),
                }
            )
    return artifacts


def build_private_regrade_attestation(
    *,
    result_paths: list[Path],
    report: RegradeReport,
    repo: str,
    pr: int,
    archive_commit: str,
    archive_ref: str = "main",
    workflow: str = "verify-private-submission",
) -> dict[str, Any]:
    if not report.ok:
        raise ValueError("cannot attest a failed private regrade report")

    by_result: dict[str, list[dict[str, Any]]] = {}
    for artifact in report.source_artifacts:
        by_result.setdefault(artifact.result_path, []).append(
            {
                "row_index": artifact.row_index,
                "task_id": artifact.task_id,
                "seed": artifact.seed,
                "track": artifact.track,
                "path": artifact.source_path,
                "sha256": artifact.sha256,
                "source_origin": artifact.source_origin,
            }
        )

    results = []
    for path in result_paths:
        result_path = _public_result_path(path)
        results.append(
            {
                "path": result_path,
                "payload_sha256": result_payload_sha256(path),
                "verification_status": "public-regrade-verified",
                "source_artifacts": by_result.get(result_path, []),
            }
        )

    return {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "kind": ATTESTATION_KIND,
        "repo": repo,
        "pr": pr,
        "workflow": workflow,
        "archive": {
            "repo": "makerbench-submissions",
            "ref": archive_ref,
            "commit": archive_commit,
        },
        "checked_rows": report.checked_rows,
        "private_rows": report.private_rows,
        "results": results,
    }


def format_attestation_comment(attestation: dict[str, Any]) -> str:
    payload = json.dumps(attestation, indent=2, sort_keys=True)
    return (
        f"{ATTESTATION_MARKER}\n"
        "Private submission artifacts were regraded from the private archive. "
        "Public CI may verify this metadata-only PR against the attestation below.\n\n"
        "```json\n"
        f"{payload}\n"
        "```"
    )


def fetch_pr_comments(repo: str, pr: int) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = json.loads(proc.stdout or "[]")
    if payload and isinstance(payload[0], list):
        return [comment for page in payload for comment in page]
    return payload


def load_comments(path: Path | str | None, *, repo: str, pr: int) -> list[dict[str, Any]]:
    if path is None:
        return fetch_pr_comments(repo, pr)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def trusted_attestations(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attestations: list[dict[str, Any]] = []
    for comment in comments:
        author_association = str(comment.get("author_association") or "")
        user = comment.get("user") or {}
        login = str(user.get("login") or "")
        if author_association not in TRUSTED_AUTHOR_ASSOCIATIONS and login not in TRUSTED_BOT_LOGINS:
            continue
        body = str(comment.get("body") or "")
        attestation = _attestation_from_comment_body(body)
        if attestation is not None:
            attestations.append(attestation)
    return attestations


def fetch_pr_author_association(repo: str, pr: int) -> str:
    """Return the PR author's repository association (OWNER/MEMBER/.../NONE)."""
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}", "--jq", ".author_association"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout.strip()


def verify_result_attestations(
    result_paths: list[Path],
    *,
    comments: list[dict[str, Any]],
    repo: str,
    pr: int,
    require_verified: bool = True,
    pr_author_association: str | None = None,
) -> list[str]:
    problems: list[str] = []
    # `result_provenance="official"` skips the community attestation requirement
    # entirely, but it is set purely from committed JSON — so an untrusted PR
    # could self-declare it to bypass the gate. Honor the bypass only when the PR
    # author is a trusted association; otherwise it is a violation (#221).
    official_author_trusted = (
        pr_author_association is not None
        and pr_author_association in TRUSTED_AUTHOR_ASSOCIATIONS
    )
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for attestation in trusted_attestations(comments):
        if attestation.get("kind") != ATTESTATION_KIND:
            continue
        if attestation.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
            continue
        if attestation.get("repo") != repo or int(attestation.get("pr", -1)) != pr:
            continue
        for result in attestation.get("results") or []:
            key = (str(result.get("path") or ""), str(result.get("payload_sha256") or ""))
            by_key[key] = result

    for path in result_paths:
        if not Path(path).exists():
            continue
        rel = _public_result_path(path)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("result_provenance", "community") == "official":
            if official_author_trusted:
                continue
            problems.append(
                f"{rel}: result_provenance='official' is maintainer-only; a PR "
                f"authored by association {pr_author_association or 'UNKNOWN'!r} "
                "may not self-declare official provenance to bypass attestation"
            )
            continue
        status = payload.get("verification_status", "unverified")
        if status != "public-regrade-verified":
            if require_verified:
                problems.append(
                    f"{rel}: community result must be maintainer-attested and "
                    "verification_status='public-regrade-verified'"
                )
            continue

        digest = result_payload_sha256(path)
        attested = by_key.get((rel, digest))
        if attested is None:
            problems.append(f"{rel}: missing trusted private regrade attestation for payload {digest}")
            continue

        declared = _artifact_set(source_artifacts_for_result(path))
        attested_artifacts = _artifact_set(attested.get("source_artifacts") or [])
        if declared != attested_artifacts:
            problems.append(
                f"{rel}: attested source artifact path/sha set does not match result dossier"
            )
    return problems


def _artifact_set(artifacts: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(artifact.get("path") or ""), str(artifact.get("sha256") or ""))
        for artifact in artifacts
        if artifact.get("path") and artifact.get("sha256")
    }


def _public_result_path(path: Path | str) -> str:
    parts = Path(path).parts
    if "results" in parts:
        return Path(*parts[parts.index("results"):]).as_posix()
    return Path(path).as_posix()


def _attestation_from_comment_body(body: str) -> dict[str, Any] | None:
    marker_index = body.find(ATTESTATION_MARKER)
    if marker_index < 0:
        return None
    rest = body[marker_index + len(ATTESTATION_MARKER):]
    fence = rest.find("```")
    if fence >= 0:
        fenced = rest[fence + 3:]
        if fenced.lstrip().startswith("json"):
            fenced = fenced.lstrip()[4:]
        end = fenced.find("```")
        if end >= 0:
            rest = fenced[:end]
    start = rest.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(rest[start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
