"""
arena.render — Render adapter stub (#423-lite).

Shells to ``openscad`` to render a .scad file to PNG/STL IF openscad is present
on PATH.  If openscad is absent (the expected state in CI), the adapter records
an ``AUTO_FAIL`` result without raising — the arena can continue producing
pairwise votes even when rendering is unavailable.

Design note: non-rendering outputs are auto-losses in objective scoring (see epic
#421 red-team notes: "generated OpenSCAD frequently won't compile/render; non-
rendering outputs must be auto-losses or the arena starves for pairs").

The subjective Elo track (#425) can still operate on the raw .scad text when
rendering is unavailable — that path is not gated on this adapter.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class RenderStatus(str, Enum):
    """Outcome of a render attempt."""

    OK = "ok"
    """Render succeeded; output file(s) are present."""

    OPENSCAD_NOT_FOUND = "openscad_not_found"
    """``openscad`` binary not on PATH; auto-fail recorded."""

    COMPILE_ERROR = "compile_error"
    """OpenSCAD reported a compile or geometry error."""

    TIMEOUT = "timeout"
    """Render exceeded ``timeout_seconds``."""

    UNKNOWN_ERROR = "unknown_error"
    """Unexpected subprocess failure."""

    @property
    def is_auto_fail(self) -> bool:
        """True when the render failure should count as an objective loss."""
        return self != RenderStatus.OK


@dataclass
class RenderResult:
    """Output of a single render attempt.

    Attributes
    ----------
    status:
        Outcome enum.
    scad_path:
        Path to the .scad source file written to disk (always set if scad_source given).
    png_path:
        Path to the rendered PNG, or None if rendering failed.
    stl_path:
        Path to the rendered STL, or None if geometry export failed.
    stderr:
        Raw stderr from the OpenSCAD subprocess (useful for debugging compile errors).
    render_seconds:
        Wall-clock seconds for the render subprocess (None if skipped).
    is_objective_fail:
        Convenience alias for ``status.is_auto_fail``.
    """

    status: RenderStatus
    scad_path: Optional[Path] = None
    png_path: Optional[Path] = None
    stl_path: Optional[Path] = None
    stderr: str = ""
    render_seconds: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_objective_fail(self) -> bool:
        return self.status.is_auto_fail


OPENSCAD_TIMEOUT_DEFAULT = 120  # seconds


def render_scad(
    scad_source: str,
    *,
    output_dir: Optional[Path] = None,
    render_png: bool = True,
    render_stl: bool = False,
    timeout_seconds: int = OPENSCAD_TIMEOUT_DEFAULT,
    openscad_bin: str = "openscad",
) -> RenderResult:
    """Render *scad_source* to PNG/STL using the system ``openscad`` binary.

    Parameters
    ----------
    scad_source:
        Raw OpenSCAD source text to render.
    output_dir:
        Directory to write outputs into.  If None a temporary directory is used
        (caller is responsible for cleanup).
    render_png:
        Whether to produce a PNG preview (default True).
    render_stl:
        Whether to produce an STL export (default False — slower).
    timeout_seconds:
        Hard kill timeout for the openscad subprocess.
    openscad_bin:
        Name or path of the openscad binary (override for testing).

    Returns
    -------
    ``RenderResult`` — never raises; all error paths are captured in ``status``.
    """
    # Step 1: check openscad is available
    if not shutil.which(openscad_bin):
        return RenderResult(
            status=RenderStatus.OPENSCAD_NOT_FOUND,
            stderr=f"'{openscad_bin}' not found on PATH — auto-fail recorded",
            metadata={"openscad_bin": openscad_bin},
        )

    # Step 2: write .scad to disk
    work_dir = output_dir or Path(tempfile.mkdtemp(prefix="arena_render_"))
    work_dir.mkdir(parents=True, exist_ok=True)
    scad_path = work_dir / "model.scad"
    scad_path.write_text(scad_source, encoding="utf-8")

    png_path: Optional[Path] = None
    stl_path: Optional[Path] = None
    stderr_text = ""

    # Step 3: render PNG
    if render_png:
        png_path = work_dir / "model.png"
        cmd = [
            openscad_bin,
            "--render",
            "--imgsize=800,600",
            "-o", str(png_path),
            str(scad_path),
        ]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            elapsed = time.monotonic() - t0
            stderr_text += proc.stderr or ""
            if proc.returncode != 0:
                return RenderResult(
                    status=RenderStatus.COMPILE_ERROR,
                    scad_path=scad_path,
                    stderr=stderr_text,
                    render_seconds=elapsed,
                    metadata={"returncode": proc.returncode},
                )
        except subprocess.TimeoutExpired:
            return RenderResult(
                status=RenderStatus.TIMEOUT,
                scad_path=scad_path,
                stderr=f"Timed out after {timeout_seconds}s",
                render_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return RenderResult(
                status=RenderStatus.UNKNOWN_ERROR,
                scad_path=scad_path,
                stderr=str(exc),
            )

        if not png_path.exists():
            png_path = None  # openscad exited 0 but no output — treat as fail

    # Step 4: export STL (optional)
    if render_stl:
        stl_path = work_dir / "model.stl"
        cmd_stl = [openscad_bin, "-o", str(stl_path), str(scad_path)]
        try:
            proc_stl = subprocess.run(
                cmd_stl,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            stderr_text += proc_stl.stderr or ""
            if proc_stl.returncode != 0 or not stl_path.exists():
                stl_path = None
        except Exception:  # noqa: BLE001
            stl_path = None

    return RenderResult(
        status=RenderStatus.OK,
        scad_path=scad_path,
        png_path=png_path if (png_path and png_path.exists()) else None,
        stl_path=stl_path,
        stderr=stderr_text,
        render_seconds=elapsed if render_png else None,
    )
