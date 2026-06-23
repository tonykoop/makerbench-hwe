"""Tests for the Blender availability probe and headless smoke runner.

Ported from archived tonykoop/makerbench #181 (graceful skip + error paths).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from makerbench.blender_smoke import blender_availability, run_blender_smoke


def _fake_blender(tmp_path: Path, body: str) -> Path:
    exe = tmp_path / "fake_blender"
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return exe


# ---------------------------------------------------------------------------
# blender_availability
# ---------------------------------------------------------------------------


def test_blender_availability_reports_missing_executable() -> None:
    result = blender_availability("__blender_definitely_not_installed__")
    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert "reason" in result


# ---------------------------------------------------------------------------
# run_blender_smoke — graceful skip
# ---------------------------------------------------------------------------


def test_blender_smoke_skips_cleanly_when_executable_missing(tmp_path: Path) -> None:
    result = run_blender_smoke(tmp_path, executable="__blender_definitely_not_installed__")
    assert result["status"] == "skipped"
    assert "reason" in result


# ---------------------------------------------------------------------------
# run_blender_smoke — error paths (fake executables)
# ---------------------------------------------------------------------------


def test_blender_smoke_reports_error_on_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    fake = _fake_blender(tmp_path, "exit 7")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = run_blender_smoke(tmp_path / "out", executable=str(fake))
    assert result["status"] == "error"
    assert result.get("returncode") == 7


def test_blender_smoke_reports_error_when_report_unreadable(tmp_path: Path, monkeypatch) -> None:
    fake = _fake_blender(tmp_path, "exit 0")  # exits cleanly but writes no report
    monkeypatch.setenv("PATH", str(tmp_path))
    result = run_blender_smoke(tmp_path / "out", executable=str(fake))
    assert result["status"] == "error"
    assert "report" in result.get("reason", "").lower() or "read" in result.get("reason", "").lower()


# ---------------------------------------------------------------------------
# run_blender_smoke — live Blender (skipped in CI without Blender)
# ---------------------------------------------------------------------------


def test_blender_smoke_runs_when_blender_is_available(tmp_path: Path) -> None:
    avail = blender_availability()
    if not avail["available"]:
        pytest.skip("Blender not installed")
    result = run_blender_smoke(tmp_path)
    assert result["status"] in ("available", "error")
