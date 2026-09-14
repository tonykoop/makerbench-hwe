"""Reference-image gatekeeper for MakerBench Arena Studio (Issue #697 D4).

Binds an approval to the **sha256 of the reference image's bytes** plus the
approving reviewer's timestamp — not just a task id. Swapping the image on
disk after approval invalidates it automatically, because the recorded hash
no longer matches the live file; there is no separate "dirty" flag to forget
to check.

Persisted outside the served tree (``.makerbench/reference_approvals.json``,
never under ``static/`` or a run directory) so nothing serves it by accident.

Replaces the plain ``{task_id: bool}`` approvals file this module's
predecessor used: :func:`migrate_legacy_approvals` treats every legacy entry
as **unapproved**, because a bare boolean carries no hash to trust.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

SCHEMA = "makerbench-arena-studio-gatekeeper-v1"
MIGRATION_REVIEWER = "migration:no-hash"


@dataclass(frozen=True)
class ApprovalRecord:
    """One task's approval decision, bound to the image bytes it was made on."""

    task_id: str
    image_sha256: str
    approved: bool
    decided_at: str
    reviewer: str = "tony"

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "image_sha256": self.image_sha256,
            "approved": self.approved,
            "decided_at": self.decided_at,
            "reviewer": self.reviewer,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ApprovalRecord":
        return cls(
            task_id=str(payload.get("task_id") or ""),
            image_sha256=str(payload.get("image_sha256") or ""),
            approved=bool(payload.get("approved")),
            decided_at=str(payload.get("decided_at") or ""),
            reviewer=str(payload.get("reviewer") or "tony"),
        )


def compute_image_hash(image_path: Path) -> str:
    """sha256 hex digest of a reference image's bytes."""
    return hashlib.sha256(Path(image_path).read_bytes()).hexdigest()


def _now_iso(now: Optional[Callable[[], datetime]] = None) -> str:
    return (now or (lambda: datetime.now(timezone.utc)))().isoformat()


def load_approvals(path: Path) -> dict[str, ApprovalRecord]:
    """Load the gatekeeper store, migrating a legacy ``{task_id: bool}`` file.

    A legacy file has no ``schema`` key. Every one of its entries becomes an
    unapproved record — see :func:`migrate_legacy_approvals` for why.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
        return {
            task_id: ApprovalRecord.from_dict(record)
            for task_id, record in (raw.get("approvals") or {}).items()
            if isinstance(record, Mapping)
        }

    if isinstance(raw, dict):
        # Legacy {task_id: bool} shape (pre-#697 D4).
        return migrate_legacy_approvals(raw)

    return {}


def migrate_legacy_approvals(legacy: Mapping[str, object]) -> dict[str, ApprovalRecord]:
    """A legacy ``{task_id: bool}`` entry carries no hash, so it cannot be
    trusted to still describe the image currently on disk — every migrated
    entry becomes ``approved=False`` regardless of its old value.
    """
    now = _now_iso()
    return {
        str(task_id): ApprovalRecord(
            task_id=str(task_id),
            image_sha256="",
            approved=False,
            decided_at=now,
            reviewer=MIGRATION_REVIEWER,
        )
        for task_id in legacy
    }


def save_approvals(path: Path, approvals: Mapping[str, ApprovalRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "approvals": {task_id: record.as_dict() for task_id, record in approvals.items()},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def approve(
    approvals: dict[str, ApprovalRecord],
    task_id: str,
    image_path: Path,
    *,
    reviewer: str = "tony",
    now: Optional[Callable[[], datetime]] = None,
) -> ApprovalRecord:
    """Approve ``task_id``'s *current* reference image, binding to its byte hash."""
    record = ApprovalRecord(
        task_id=task_id,
        image_sha256=compute_image_hash(image_path),
        approved=True,
        decided_at=_now_iso(now),
        reviewer=reviewer,
    )
    approvals[task_id] = record
    return record


def revoke(
    approvals: dict[str, ApprovalRecord],
    task_id: str,
    *,
    reviewer: str = "tony",
    now: Optional[Callable[[], datetime]] = None,
) -> Optional[ApprovalRecord]:
    """Revoke an existing approval. No-op (returns ``None``) if none exists."""
    existing = approvals.get(task_id)
    if existing is None:
        return None
    record = ApprovalRecord(
        task_id=task_id,
        image_sha256=existing.image_sha256,
        approved=False,
        decided_at=_now_iso(now),
        reviewer=reviewer,
    )
    approvals[task_id] = record
    return record


def is_approved(
    approvals: Mapping[str, ApprovalRecord],
    task_id: str,
    image_path: Optional[Path],
) -> bool:
    """True only if the task is approved *and* the live image still matches
    the hash it was approved on. A swapped-in image, a missing file, or no
    record at all are all "not approved" — never a default-True.
    """
    record = approvals.get(task_id)
    if record is None or not record.approved:
        return False
    if not record.image_sha256:
        return False  # migrated legacy record: never trust a hashless approval
    if image_path is None or not Path(image_path).is_file():
        return False
    return compute_image_hash(image_path) == record.image_sha256
