"""Blender availability probe and headless smoke runner.

``blender_availability`` resolves the Blender executable and reports whether
it is present without running it.  ``run_blender_smoke`` executes a minimal
headless Blender scene to confirm that the install is functional; it returns
``status: "skipped"`` when Blender is not installed rather than raising, so CI
environments without Blender do not fail.

Ported from archived tonykoop/makerbench #181 (graceful skip + error paths).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def blender_availability(executable: str = "blender") -> dict[str, Any]:
    """Return availability metadata for the Blender executable.

    Args:
        executable: Name or path of the Blender binary.

    Returns:
        dict with keys ``available`` (bool), ``status`` (str), ``executable``
        (str), and optionally ``reason`` (str) when unavailable.
    """
    resolved = shutil.which(executable)
    if resolved is None:
        return {
            "available": False,
            "status": "unavailable",
            "reason": f"'{executable}' not found on PATH",
            "executable": executable,
        }
    return {
        "available": True,
        "status": "available",
        "executable": resolved,
    }


def run_blender_smoke(
    output_dir: Path | str,
    *,
    executable: str = "blender",
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Run a minimal headless Blender smoke scene and return a status dict.

    The smoke scene script is resolved relative to this module as
    ``../examples/blender_mcp_stack/tasks/smoke_scene.py``.  When Blender is
    not available the result carries ``status: "skipped"`` so callers do not
    need to gate on availability themselves.

    Args:
        output_dir: Directory where Blender writes its smoke report JSON.
        executable: Blender binary name or path.
        timeout_s: Seconds before the subprocess is killed.

    Returns:
        dict with at minimum a ``status`` key: ``"available"`` on success,
        ``"skipped"`` when Blender is absent, or ``"error"`` on failure.
    """
    avail = blender_availability(executable)
    if not avail["available"]:
        return {"status": "skipped", "reason": avail["reason"], "executable": executable}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "smoke_report.json"

    # Smoke scene script lives alongside the blender_mcp_stack examples.
    script_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "blender_mcp_stack"
        / "tasks"
        / "smoke_scene.py"
    )

    cmd = [
        avail["executable"],
        "--background",
        "--python", str(script_path),
        "--",
        "--output", str(report_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "reason": f"Blender smoke timed out after {timeout_s}s",
            "executable": avail["executable"],
        }

    if result.returncode != 0:
        return {
            "status": "error",
            "reason": f"Blender exited with code {result.returncode}",
            "executable": avail["executable"],
            "returncode": result.returncode,
        }

    try:
        report = json.loads(report_path.read_text())
    except Exception as exc:
        return {
            "status": "error",
            "reason": f"smoke report could not be read: {exc}",
            "executable": avail["executable"],
        }

    return {"status": "available", "executable": avail["executable"], **report}
