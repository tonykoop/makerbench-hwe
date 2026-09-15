"""Sandboxed OpenSCAD compiler for the design workbench (#788, W0).

``code_cad_objective.compile_scad_to_artifacts`` runs ``openscad`` directly on
the host through ``render._run``. OpenSCAD cannot spawn processes, but
``include``/``use``/``import()``/``surface()`` read any host path the process
can, so code that a person or a model edited in the Studio must never compile
that way. This module compiles one ``.scad`` file inside an unprivileged
Bubblewrap mount namespace instead:

- ``/usr`` (plus the usrmerge links), ``/proc``, ``/dev`` and a size-capped
  tmpfs ``/tmp`` are the whole filesystem. ``/``, ``/mnt``, ``$HOME``, the
  repository, ``runs/`` and ``private/`` are never mounted.
- Only the **single source file** is copied into a scratch directory that is
  bound read-only at ``/work``. Siblings of the original file are not visible,
  so a relative ``include <../x.scad>`` fails inside the sandbox even when it
  would work on the host. The instrument masters use no includes today.
- The output directory is the only writable bind (``/out``).
- User, PID, IPC, UTS and **network** namespaces are unshared, the environment
  is cleared to a short allow-list, and ``--die-with-parent`` ties the
  sandbox to the caller so a timeout kills everything.
- The preview PNG needs an X server, so ``xvfb-run`` runs *inside* the sandbox
  (it lives under ``/usr``). A zero-byte PNG or mesh is a failure, never a
  success: OpenSCAD exits 0 with an empty PNG when it has no display.

Environment failures (no bwrap, no openscad, no xvfb-run) raise
:class:`SandboxUnavailable` (a ``RuntimeError``) so callers never mis-score
infrastructure as a candidate defect, and nothing falls back to the host.
Candidate defects raise :class:`makerbench.render.CompileError` exactly like
the host compiler.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import render
from .code_cad_objective import RenderArtifacts
from .entrant_sandbox import SandboxUnavailable, _bwrap, _host_env, _runtime_args

__all__ = [
    "SandboxUnavailable",
    "OPENSCAD_TIMEOUT_ENV",
    "OUTPUT_CAP_ENV",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_OUTPUT_CAP_BYTES",
    "sandbox_available",
    "build_command",
    "compile_scad_sandboxed",
]

#: Same override the host compiler honours, so the budget is one number.
OPENSCAD_TIMEOUT_ENV = "MAKERBENCH_OPENSCAD_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 120
#: Total bytes the sandbox may leave in the output directory.
OUTPUT_CAP_ENV = "MAKERBENCH_SCAD_SANDBOX_OUTPUT_CAP_BYTES"
DEFAULT_OUTPUT_CAP_BYTES = 200 * 1024 * 1024
#: Cap on the tmpfs ``/tmp`` the sandbox writes scratch into.
TMPFS_SIZE_BYTES = 256 * 1024 * 1024
#: Address-space limit for the sandboxed process tree. CGAL can be hungry, so
#: this only stops runaway allocation, not heavy legitimate CSG.
ADDRESS_SPACE_LIMIT_BYTES = 8 * 1024 * 1024 * 1024
#: Largest single file the sandbox may write.
FILE_SIZE_LIMIT_BYTES = 512 * 1024 * 1024

WORK_DIR = "/work"
OUT_DIR = "/out"
SOURCE_NAME = "input.scad"
STL_NAME = "output.stl"
PNG_NAME = "preview.png"

ISOLATION_FLAGS = (
    "--die-with-parent",
    "--new-session",
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--unshare-net",
)

#: Read-only, non-secret host directories OpenSCAD's PNG path consults.
_ETC_RO_DIRS = ("/etc/fonts",)

_SANDBOX_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "TMPDIR": "/tmp",
}

PNG_ARGS = (
    "--viewall",
    "--autocenter",
    "--colorscheme=Tomorrow",
    "--imgsize=800,600",
)


def _openscad() -> str | None:
    return shutil.which("openscad")


def _xvfb_run() -> str | None:
    return shutil.which("xvfb-run")


def sandbox_available() -> bool:
    """Whether bwrap, openscad and xvfb-run are present and an unprivileged
    sandbox with these isolation flags can start. Probed on every call."""

    bwrap = _bwrap()
    if bwrap is None or _openscad() is None or _xvfb_run() is None:
        return False
    if not Path("/usr/bin/true").exists():
        return False
    try:
        probe = subprocess.run(
            [bwrap, *ISOLATION_FLAGS, *_runtime_args(), "/usr/bin/true"],
            capture_output=True,
            text=True,
            timeout=10,
            env=_host_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def build_command(
    *,
    work_dir: Path,
    out_dir: Path,
    openscad_args: Sequence[str],
    with_xvfb: bool,
) -> list[str]:
    """The bwrap argv that runs ``openscad`` (optionally under ``xvfb-run``)
    with ``work_dir`` read-only at ``/work`` and ``out_dir`` writable at
    ``/out``. Pure: it touches no files."""

    bwrap = _bwrap()
    if bwrap is None:
        raise SandboxUnavailable("bubblewrap (bwrap) is not installed")
    openscad = _openscad()
    if openscad is None:
        raise SandboxUnavailable("openscad is not installed")
    work_dir = Path(work_dir)
    out_dir = Path(out_dir)
    if not work_dir.is_absolute() or not out_dir.is_absolute():
        raise SandboxUnavailable("work_dir and out_dir must be absolute")
    if work_dir.resolve() == out_dir.resolve():
        raise SandboxUnavailable("work_dir and out_dir must differ")

    cmd: list[str] = [bwrap, *ISOLATION_FLAGS]
    runtime = _runtime_args()
    # Insert the tmpfs size cap in front of the ``--tmpfs /tmp`` the helper adds.
    tmpfs_at = runtime.index("--tmpfs")
    runtime[tmpfs_at:tmpfs_at] = ["--size", str(TMPFS_SIZE_BYTES)]
    cmd += runtime
    for name in _ETC_RO_DIRS:
        if Path(name).is_dir():
            cmd += ["--ro-bind", name, name]
    cmd += ["--ro-bind", work_dir.as_posix(), WORK_DIR]
    cmd += ["--bind", out_dir.as_posix(), OUT_DIR]
    cmd += ["--chdir", WORK_DIR, "--clearenv"]
    for key in sorted(_SANDBOX_ENV):
        cmd += ["--setenv", key, _SANDBOX_ENV[key]]
    cmd.append("--")
    if with_xvfb:
        xvfb_run = _xvfb_run()
        if xvfb_run is None:
            raise SandboxUnavailable("xvfb-run is not installed (needed for the preview PNG)")
        cmd += [xvfb_run, "-a"]
    cmd += [openscad, *openscad_args]
    return cmd


def _apply_rlimits() -> None:
    """Runs in the child before exec; bwrap and OpenSCAD inherit these."""

    for kind, limit in (
        (resource.RLIMIT_AS, ADDRESS_SPACE_LIMIT_BYTES),
        (resource.RLIMIT_FSIZE, FILE_SIZE_LIMIT_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        try:
            soft, hard = resource.getrlimit(kind)
            new = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(kind, (new, hard))
        except (ValueError, OSError):
            # A stricter inherited hard limit already applies; keep it.
            continue


def _timeout_s() -> int:
    timeout = int(os.environ.get(OPENSCAD_TIMEOUT_ENV, str(DEFAULT_TIMEOUT_S)))
    if timeout <= 0:
        raise ValueError(f"{OPENSCAD_TIMEOUT_ENV} must be a positive integer")
    return timeout


def _output_cap_bytes() -> int:
    cap = int(os.environ.get(OUTPUT_CAP_ENV, str(DEFAULT_OUTPUT_CAP_BYTES)))
    if cap <= 0:
        raise ValueError(f"{OUTPUT_CAP_ENV} must be a positive integer")
    return cap


def _tree_bytes(directory: Path) -> int:
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def _run_sandboxed(cmd: list[str], *, timeout: int, stage: str) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_host_env(),
            check=False,
            preexec_fn=_apply_rlimits,
        )
    except subprocess.TimeoutExpired as exc:
        raise render.CompileError(f"openscad {stage} timed out after {timeout}s") from exc
    if proc.returncode != 0 and proc.stderr.lstrip().startswith("bwrap:"):
        raise SandboxUnavailable(f"openscad sandbox failed: {proc.stderr.strip()[-600:]}")
    return proc


def _check_output_cap(out_dir: Path, cap: int, stage: str) -> None:
    used = _tree_bytes(out_dir)
    if used > cap:
        for path in out_dir.iterdir():
            if path.is_file():
                path.unlink()
        raise render.CompileError(
            f"openscad {stage} output ({used} bytes) exceeds the sandbox cap of {cap} bytes"
        )


def compile_scad_sandboxed(scad_path: Path, out_dir: Path) -> RenderArtifacts:
    """Compile one OpenSCAD source to STL plus preview PNG inside Bubblewrap.

    Same signature and artifact layout as ``compile_scad_to_artifacts``
    (``output.stl`` and ``preview.png`` under ``out_dir``), so it drops into
    ``evaluate_objective_trial`` unchanged.
    """

    timeout = _timeout_s()
    cap = _output_cap_bytes()
    scad_path = Path(scad_path).resolve(strict=True)
    if not scad_path.is_file():
        raise render.CompileError(f"not a file: {scad_path.name}")
    if not sandbox_available():
        raise SandboxUnavailable(
            "openscad sandbox unavailable: install bubblewrap, openscad and xvfb, "
            "and enable unprivileged user namespaces"
        )
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stl_path = out_dir / STL_NAME
    png_path = out_dir / PNG_NAME
    for stale in (stl_path, png_path):
        if stale.exists():
            stale.unlink()

    with tempfile.TemporaryDirectory(prefix="makerbench-scad-sandbox-") as tmp:
        work_dir = Path(tmp) / "work"
        work_dir.mkdir()
        # Only the one source file crosses into the sandbox.
        shutil.copyfile(scad_path, work_dir / SOURCE_NAME)
        source_in = f"{WORK_DIR}/{SOURCE_NAME}"

        mesh_cmd = build_command(
            work_dir=work_dir,
            out_dir=out_dir,
            openscad_args=["-o", f"{OUT_DIR}/{STL_NAME}", source_in],
            with_xvfb=False,
        )
        proc = _run_sandboxed(mesh_cmd, timeout=timeout, stage="mesh export")
        warnings = [
            ln for ln in proc.stderr.splitlines() if "WARNING" in ln or "DEPRECATED" in ln
        ]
        if proc.returncode != 0 or not stl_path.is_file():
            raise render.CompileError(
                f"OpenSCAD exited {proc.returncode}.\nSTDERR:\n{proc.stderr.strip()}"
            )
        if stl_path.stat().st_size == 0:
            raise render.CompileError("OpenSCAD produced an empty mesh (no geometry).")
        _check_output_cap(out_dir, cap, "mesh export")

        png_cmd = build_command(
            work_dir=work_dir,
            out_dir=out_dir,
            openscad_args=["-o", f"{OUT_DIR}/{PNG_NAME}", *PNG_ARGS, source_in],
            with_xvfb=True,
        )
        proc = _run_sandboxed(png_cmd, timeout=timeout, stage="preview render")
        if proc.returncode != 0 or not png_path.is_file() or png_path.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout).strip()[-1200:]
            raise render.CompileError(
                f"Render failed (rc={proc.returncode}, empty or missing PNG): {detail}"
            )
        _check_output_cap(out_dir, cap, "preview render")

    return RenderArtifacts(stl_path=stl_path, png_path=png_path, warnings=tuple(warnings))
