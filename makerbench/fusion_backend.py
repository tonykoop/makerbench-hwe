"""Fusion 360 CAD-backend axis compiler (#627, follow-up to #601): a Windows
job-dir handoff, not a WSL-native compile.

Fusion's API (``adsk.core``/``adsk.fusion``) only runs inside Fusion 360
itself, on Windows. This module turns an entrant's Fusion-API Python script
into ``RenderArtifacts`` by handing it to ``makerbench.jobdir_backend``'s
WSL<->Windows job-dir protocol: write the job, poll ``status.json`` for the
Windows-side watcher (``scripts/windows/fusion_job_watcher.ps1``, UNVALIDATED
— see its header) to finish, then adapt the exported STL/PNG paths it
reports.

Matches the ``Compiler`` shape — ``(source_path, out_dir) -> RenderArtifacts``
— every other CAD backend in ``code_cad_arena_runner.BACKEND_COMPILERS``
implements, so the mesh gate/vote surface/Elo pipeline need zero
special-casing for this backend. Raises ``render.CompileError`` on any
candidate-or-environment-caused failure (bad script, no geometry, export
failure, timeout) — never crashes.

Unlike SolidWorks, Fusion's export API takes an explicit unit/scale
parameter, so there is no equivalent "inches by default" gotcha to guard
against here — the Windows watcher's export step is expected to request mm
directly (see the watcher script's header comment).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from . import jobdir_backend
from . import render
from .code_cad_objective import RenderArtifacts


def fusion_jobdir_available() -> bool:
    """Preflight: does the job-dir handoff path exist?

    See ``jobdir_backend.jobdir_handoff_available`` — this only confirms the
    filesystem bridge to Windows exists, not that Fusion 360 is installed,
    licensed, or that a watcher is running. WSL cannot check the latter.
    """

    return jobdir_backend.jobdir_handoff_available()


def compile_fusion_to_artifacts(
    source_path: Path,
    out_dir: Path,
    *,
    timeout_s: Optional[float] = None,
    poll_interval_s: Optional[float] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> RenderArtifacts:
    """Hand a Fusion-API Python script to the job-dir runner and return the artifacts.

    ``timeout_s``/``poll_interval_s`` default to
    ``MAKERBENCH_JOBDIR_TIMEOUT_S``/``MAKERBENCH_JOBDIR_POLL_INTERVAL_S``
    (falling back to ``jobdir_backend``'s defaults). ``sleep_fn``/``clock_fn``
    exist for deterministic tests only.
    """

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trial_id = f"fusion-{out_dir.name}"

    jdir = jobdir_backend.create_job(
        trial_id, Path(source_path), backend="fusion", source_filename="entrant.py"
    )

    timeout = timeout_s if timeout_s is not None else float(
        os.environ.get("MAKERBENCH_JOBDIR_TIMEOUT_S", jobdir_backend.DEFAULT_TIMEOUT_S)
    )
    poll_interval = poll_interval_s if poll_interval_s is not None else float(
        os.environ.get("MAKERBENCH_JOBDIR_POLL_INTERVAL_S", jobdir_backend.DEFAULT_POLL_INTERVAL_S)
    )

    payload = jobdir_backend.poll_job(
        jdir,
        timeout_s=timeout,
        poll_interval_s=poll_interval,
        sleep_fn=sleep_fn,
        clock_fn=clock_fn,
    )

    stl_path = Path(str(payload["stl_path"]))
    png_reported = payload.get("png_path")
    png_path = Path(str(png_reported)) if png_reported else out_dir / "preview.missing.png"

    if stl_path.stat().st_size == 0:
        raise render.CompileError("fusion watcher exported an empty STL (no geometry).")

    return RenderArtifacts(stl_path=stl_path, png_path=png_path, warnings=())
