"""Redacted preflight doctor for the nightly CAD arena (#658).

Audits activation state without ever printing secret values:

- Secrets: each required key is PRESENT / MISSING / PLACEHOLDER.
- Lock: ``runs/.nightly-cad.lock`` — absent, held by a live PID, or stale.
- Queue: per-job status summary (job ids + statuses, blocked jobs called out).
- Paths: runner script, repo root, queue, output root, instruments root.

Read-only by design: no queue or lock mutation code paths live here. The
report is safe to paste into a GO review — values never leave this module,
only classifications.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

REQUIRED_SECRET_KEYS: tuple[str, ...] = (
    "CADAM_USER_ID",
    "CADAM_ACCESS_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
)

# Values that are clearly stand-ins rather than real credentials.
_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:changeme|change[-_]me|placeholder|todo|fixme|dummy|example|test|"
    r"your[-_].*|<[^>]*>|x{3,}|\*{3,}|\.{3,})$",
    re.IGNORECASE,
)

SECRET_PRESENT = "PRESENT"
SECRET_MISSING = "MISSING"
SECRET_PLACEHOLDER = "PLACEHOLDER"

LOCK_ABSENT = "ABSENT"
LOCK_ACTIVE = "ACTIVE"
LOCK_STALE = "STALE"
LOCK_UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class SecretStatus:
    key: str
    status: str  # PRESENT / MISSING / PLACEHOLDER

    @property
    def ok(self) -> bool:
        return self.status == SECRET_PRESENT


@dataclass(frozen=True)
class LockStatus:
    status: str  # ABSENT / ACTIVE / STALE / UNREADABLE
    pid: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == LOCK_ABSENT


@dataclass(frozen=True)
class QueueStatus:
    ok: bool
    error: Optional[str] = None
    jobs: tuple[tuple[str, str], ...] = ()  # (job_id, status)

    @property
    def blocked_job_ids(self) -> tuple[str, ...]:
        return tuple(job_id for job_id, status in self.jobs if status == "blocked")


@dataclass(frozen=True)
class PathStatus:
    name: str
    path: Path
    exists: bool


@dataclass(frozen=True)
class PreflightReport:
    secrets: tuple[SecretStatus, ...]
    lock: LockStatus
    queue: QueueStatus
    paths: tuple[PathStatus, ...] = field(default_factory=tuple)

    @property
    def secrets_ok(self) -> bool:
        return all(item.ok for item in self.secrets)

    @property
    def paths_ok(self) -> bool:
        return all(item.exists for item in self.paths)

    @property
    def ok(self) -> bool:
        return self.secrets_ok and self.lock.ok and self.queue.ok and self.paths_ok


def classify_secret_value(value: Optional[str]) -> str:
    """Classify a secret's value WITHOUT retaining or returning it."""

    if value is None:
        return SECRET_MISSING
    stripped = value.strip()
    if not stripped or _PLACEHOLDER_PATTERN.match(stripped):
        return SECRET_PLACEHOLDER
    return SECRET_PRESENT


def parse_secrets_file(path: Path) -> dict[str, str]:
    """Parse a bash-sourceable ``KEY=value`` env file (values stay in-process)."""

    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        entries[key] = value
    return entries


def audit_secrets(
    secrets_path: Path,
    required_keys: Sequence[str] = REQUIRED_SECRET_KEYS,
) -> tuple[SecretStatus, ...]:
    if not secrets_path.exists():
        return tuple(SecretStatus(key, SECRET_MISSING) for key in required_keys)
    entries = parse_secrets_file(secrets_path)
    return tuple(
        SecretStatus(key, classify_secret_value(entries.get(key)))
        for key in required_keys
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def audit_lock(
    lock_path: Path,
    *,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> LockStatus:
    """Ownership check only — never mutates or removes the lock."""

    if not lock_path.exists():
        return LockStatus(LOCK_ABSENT)
    try:
        import json

        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (ValueError, KeyError, TypeError, OSError):
        return LockStatus(LOCK_UNREADABLE)
    return LockStatus(LOCK_ACTIVE if pid_alive(pid) else LOCK_STALE, pid=pid)


def audit_queue(queue_path: Path) -> QueueStatus:
    if not queue_path.exists():
        return QueueStatus(ok=False, error=f"queue file missing: {queue_path}")
    try:
        import json

        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        raw_jobs = payload.get("jobs") or []
        jobs = tuple(
            (
                str(job.get("job_id") or f"<unnamed-{index}>"),
                str(job.get("status") or "queued"),
            )
            for index, job in enumerate(raw_jobs)
        )
    except (ValueError, OSError) as exc:
        return QueueStatus(ok=False, error=f"queue unreadable: {exc}")
    blocked = tuple(job_id for job_id, status in jobs if status == "blocked")
    return QueueStatus(ok=not blocked, jobs=jobs, error=None)


def audit_paths(named_paths: Mapping[str, Path]) -> tuple[PathStatus, ...]:
    return tuple(
        PathStatus(name=name, path=Path(path), exists=Path(path).exists())
        for name, path in named_paths.items()
    )


def build_report(
    *,
    secrets_path: Path,
    lock_path: Path,
    queue_path: Path,
    named_paths: Mapping[str, Path],
    required_keys: Sequence[str] = REQUIRED_SECRET_KEYS,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> PreflightReport:
    return PreflightReport(
        secrets=audit_secrets(secrets_path, required_keys),
        lock=audit_lock(lock_path, pid_alive=pid_alive),
        queue=audit_queue(queue_path),
        paths=audit_paths(named_paths),
    )


def render_report_lines(report: PreflightReport) -> list[str]:
    """Human-readable, redaction-safe report lines (no secret values, ever)."""

    lines: list[str] = ["nightly CAD arena preflight"]
    lines.append(f"[{'PASS' if report.secrets_ok else 'FAIL'}] secrets")
    for item in report.secrets:
        lines.append(f"  {item.key}: {item.status}")
    lock = report.lock
    lock_detail = f" pid={lock.pid}" if lock.pid is not None else ""
    lines.append(f"[{'PASS' if lock.ok else 'FAIL'}] lock: {lock.status}{lock_detail}")
    queue = report.queue
    lines.append(f"[{'PASS' if queue.ok else 'FAIL'}] queue")
    if queue.error:
        lines.append(f"  {queue.error}")
    for job_id, status in queue.jobs:
        marker = " <-- blocked" if status == "blocked" else ""
        lines.append(f"  {job_id}: {status}{marker}")
    lines.append(f"[{'PASS' if report.paths_ok else 'FAIL'}] paths")
    for item in report.paths:
        state = "exists" if item.exists else "MISSING"
        lines.append(f"  {item.name}: {item.path} ({state})")
    lines.append(f"preflight: {'GO' if report.ok else 'NO-GO'}")
    return lines
