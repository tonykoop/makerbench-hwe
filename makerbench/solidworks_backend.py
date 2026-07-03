"""SolidWorks CAD-backend axis compiler (#627, follow-up to #601): a Windows
job-dir handoff, not a WSL-native compile.

SolidWorks scripting (VBA macros driving the ``SldWorks.Application`` COM
object model) only runs inside SolidWorks itself, on Windows. This module
turns an entrant's VBA macro into ``RenderArtifacts`` by handing it to
``makerbench.jobdir_backend``'s WSL<->Windows job-dir protocol: write the job,
poll ``status.json`` for the Windows-side watcher
(``scripts/windows/solidworks_job_watcher.ps1``, UNVALIDATED — see its header)
to finish, then adapt the exported STL/PNG paths it reports.

Matches the ``Compiler`` shape — ``(source_path, out_dir) -> RenderArtifacts``
— every other CAD backend in ``code_cad_arena_runner.BACKEND_COMPILERS``
implements, so the mesh gate/vote surface/Elo pipeline need zero
special-casing for this backend. Raises ``render.CompileError`` on any
candidate-or-environment-caused failure (bad macro, no geometry, export
failure, timeout) — never crashes.

SolidWorks-specific gotcha (#627): SolidWorks STEP exports are in inches.
The Windows watcher is expected to export STL directly (avoiding the
unreliable WSL-side STEP/GLB->STL conversion entirely), but as a thin sanity
check this module still normalizes to millimeters if the watcher declares
``units: "in"`` in ``status.json`` — see ``_normalize_units_to_mm`` below.
This is a minimal scale-and-rewrite, not a STEP parser.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from . import jobdir_backend
from . import render
from .code_cad_objective import RenderArtifacts

INCH_TO_MM = 25.4


def solidworks_jobdir_available() -> bool:
    """Preflight: does the job-dir handoff path exist?

    See ``jobdir_backend.jobdir_handoff_available`` — this is honestly only
    "the filesystem bridge to Windows exists", not "SolidWorks is installed
    and a watcher is running". There is no way to check the latter from WSL.
    """

    return jobdir_backend.jobdir_handoff_available()


def _normalize_units_to_mm(stl_path: Path, units: Optional[str]) -> Optional[str]:
    """If the watcher declared inch units, scale the STL to mm in place.

    Returns a warning string when a conversion happened, else ``None``. Not a
    STEP parser: SolidWorks STEP exports are documented (#627) to be in
    inches, and the Windows watcher is expected to export STL directly for
    exactly this reason (WSL-side STEP conversion is unreliable), but this is
    a cheap belt-and-suspenders check in case a watcher run still routed
    through an inch-denominated intermediate.
    """

    if units != "in":
        return None
    import trimesh

    mesh = trimesh.load(stl_path.as_posix(), force="mesh")
    mesh.apply_scale(INCH_TO_MM)
    mesh.export(stl_path.as_posix())
    return f"solidworks export declared units='in'; scaled by {INCH_TO_MM} to normalize to mm"


def compile_solidworks_to_artifacts(
    source_path: Path,
    out_dir: Path,
    *,
    timeout_s: Optional[float] = None,
    poll_interval_s: Optional[float] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> RenderArtifacts:
    """Hand a VBA macro to the SolidWorks job-dir runner and return the artifacts.

    ``timeout_s``/``poll_interval_s`` default to
    ``MAKERBENCH_JOBDIR_TIMEOUT_S``/``MAKERBENCH_JOBDIR_POLL_INTERVAL_S``
    (falling back to ``jobdir_backend``'s defaults) so a real run can tune the
    Windows round-trip budget without code changes. ``sleep_fn``/``clock_fn``
    exist for deterministic tests — a real caller never needs to pass them.
    """

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trial_id = f"solidworks-{out_dir.name}"

    jdir = jobdir_backend.create_job(
        trial_id, Path(source_path), backend="solidworks", source_filename="entrant.vba"
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

    warnings: tuple[str, ...] = ()
    conversion_note = _normalize_units_to_mm(stl_path, payload.get("units"))
    if conversion_note:
        warnings = (conversion_note,)

    if stl_path.stat().st_size == 0:
        raise render.CompileError("solidworks watcher exported an empty STL (no geometry).")

    return RenderArtifacts(stl_path=stl_path, png_path=png_path, warnings=warnings)
