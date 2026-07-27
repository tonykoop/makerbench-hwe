"""Optional-local KiCad ERC/DRC evaluator helpers.

The public PCB task can be graded without KiCad, but electronics workflows also
benefit from native KiCad diagnostics. This module wraps ``kicad-cli`` in a
small, importable API that skips cleanly when the executable is absent and emits
a structured report when ERC/DRC can run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

CheckStatus = Literal["passed", "violations", "skipped", "tool_error"]


@dataclass(frozen=True)
class KicadViolation:
    """One ERC/DRC finding normalized from KiCad's JSON report."""

    check: str
    severity: str
    message: str
    code: str = ""
    location: str = ""
    source: str = ""


@dataclass(frozen=True)
class KicadCheckReport:
    """Result for one KiCad CLI subcommand."""

    check: str
    artifact: str
    status: CheckStatus
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    report_path: str = ""
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    violations: list[KicadViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool | None:
        if self.status == "passed":
            return True
        if self.status in {"violations", "tool_error"}:
            return False
        return None


@dataclass(frozen=True)
class KicadCliReport:
    """Aggregate ERC/DRC report suitable for result metadata or diagnostics."""

    tool: str
    available: bool
    skipped: bool
    checks: list[KicadCheckReport]

    @property
    def passed(self) -> bool | None:
        if self.skipped:
            return None
        return all(check.passed is True for check in self.checks)

    @property
    def violation_count(self) -> int:
        return sum(len(check.violations) for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        payload["violation_count"] = self.violation_count
        return payload


def run_kicad_erc_drc(
    *,
    pcb_path: str | os.PathLike[str] | None = None,
    sch_path: str | os.PathLike[str] | None = None,
    work_dir: str | os.PathLike[str] | None = None,
    kicad_cli: str = "kicad-cli",
    timeout_s: float = 60.0,
) -> KicadCliReport:
    """Run KiCad ERC/DRC where possible and return a structured report.

    ``pcb_path`` triggers ``kicad-cli pcb drc`` and ``sch_path`` triggers
    ``kicad-cli sch erc``. If ``kicad-cli`` is not on PATH, the requested checks
    are reported as ``skipped`` instead of raising, so public CI and machines
    without KiCad remain usable.
    """
    requested = _requested_checks(pcb_path=pcb_path, sch_path=sch_path)
    if not requested:
        raise ValueError("at least one of pcb_path or sch_path is required")

    executable = shutil.which(kicad_cli)
    if executable is None:
        checks = [
            KicadCheckReport(
                check=check,
                artifact=str(path),
                status="skipped",
                command=_command_for(check, kicad_cli, Path("<report.json>"), path),
                error=f"{kicad_cli!r} not found on PATH; KiCad ERC/DRC is optional_local",
            )
            for check, path in requested
        ]
        return KicadCliReport(tool=kicad_cli, available=False, skipped=True, checks=checks)

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="makerbench-kicad-") as tmp:
            checks = [_run_one(check, path, Path(tmp), executable, timeout_s) for check, path in requested]
    else:
        out_dir = Path(work_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        checks = [_run_one(check, path, out_dir, executable, timeout_s) for check, path in requested]

    return KicadCliReport(tool=executable, available=True, skipped=False, checks=checks)


def _requested_checks(
    *,
    pcb_path: str | os.PathLike[str] | None,
    sch_path: str | os.PathLike[str] | None,
) -> list[tuple[str, Path]]:
    requested: list[tuple[str, Path]] = []
    if sch_path is not None:
        requested.append(("erc", Path(sch_path)))
    if pcb_path is not None:
        requested.append(("drc", Path(pcb_path)))
    return requested


def _command_for(check: str, executable: str, report_path: Path, artifact: Path) -> list[str]:
    if check == "erc":
        return [
            executable,
            "sch",
            "erc",
            "--format",
            "json",
            "--output",
            str(report_path),
            str(artifact),
        ]
    return [
        executable,
        "pcb",
        "drc",
        "--format",
        "json",
        "--output",
        str(report_path),
        str(artifact),
    ]


def _run_one(
    check: str,
    artifact: Path,
    work_dir: Path,
    executable: str,
    timeout_s: float,
) -> KicadCheckReport:
    report_path = work_dir / f"{artifact.stem}.{check}.json"
    command = _command_for(check, executable, report_path, artifact)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return KicadCheckReport(
            check=check,
            artifact=str(artifact),
            status="tool_error",
            command=command,
            report_path=str(report_path),
            error=str(exc),
        )

    data = _load_json_report(report_path, proc.stdout)
    violations = _violations_from_report(check, data) if data is not None else []
    if violations:
        status: CheckStatus = "violations"
    elif proc.returncode == 0:
        status = "passed"
    else:
        status = "tool_error"

    return KicadCheckReport(
        check=check,
        artifact=str(artifact),
        status=status,
        command=command,
        returncode=proc.returncode,
        report_path=str(report_path),
        stdout=proc.stdout,
        stderr=proc.stderr,
        error="" if status != "tool_error" else "kicad-cli exited without a parseable violation report",
        violations=violations,
    )


def _load_json_report(path: Path, stdout: str) -> Any | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    stdout = stdout.strip()
    if stdout.startswith("{") or stdout.startswith("["):
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None
    return None


def _violations_from_report(check: str, data: Any) -> list[KicadViolation]:
    candidates: list[dict[str, Any]] = []
    _collect_violation_dicts(data, candidates)
    return [_violation_from_dict(check, item) for item in candidates]


def _collect_violation_dicts(value: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_violation_dicts(item, out)
        return
    if not isinstance(value, dict):
        return

    if _looks_like_violation(value):
        out.append(value)
    for item in value.values():
        _collect_violation_dicts(item, out)


def _looks_like_violation(value: dict[str, Any]) -> bool:
    if value.get("violations") == []:
        return False
    keys = set(value)
    message_keys = {"message", "description", "text", "errorMessage", "title"}
    code_keys = {"code", "rule", "type", "id", "name"}
    return bool(keys & message_keys) and bool(keys & ({"severity"} | code_keys))


def _violation_from_dict(check: str, value: dict[str, Any]) -> KicadViolation:
    message = _first_string(value, "message", "description", "text", "errorMessage", "title")
    severity = _first_string(value, "severity", default="error")
    code = _first_string(value, "code", "rule", "type", "id", "name")
    location = _format_location(value)
    source = _first_string(value, "source", "item", "items")
    return KicadViolation(
        check=check,
        severity=severity,
        message=message,
        code=code,
        location=location,
        source=source,
    )


def _first_string(value: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        raw = value.get(key)
        if isinstance(raw, str):
            return raw
        if raw is not None and not isinstance(raw, (dict, list)):
            return str(raw)
    return default


def _format_location(value: dict[str, Any]) -> str:
    for key in ("location", "pos", "position", "at"):
        raw = value.get(key)
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            x = raw.get("x")
            y = raw.get("y")
            if x is not None and y is not None:
                return f"{x},{y}"
    return ""
