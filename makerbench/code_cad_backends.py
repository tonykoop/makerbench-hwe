"""Pluggable CAD-compile backends for the Code-CAD Arena (#627).

The arena's compile layer was OpenSCAD-only: an entrant's source became a mesh
+ preview by shelling out to the ``openscad`` binary (``code_cad_objective``).
This module generalizes that seam into a small registry so the arena can also
score entrants authored for **SolidWorks** or **Fusion 360**, whose kernels only
run on Windows and are not scriptable from the WSL/Linux side where the arena
loops.

The bridge is a *job directory* protocol (no RPC, no COM from Linux): the Python
side drops a job under ``jobs/<trial_id>/`` and polls a ``status.json`` file that
a Windows-side watcher (``scripts/arena_windows_backend_watcher.ps1``) flips to
``done``/``error`` after driving the real CAD app to export STL + preview PNG.
See ``docs/CODE_CAD_BACKENDS.md`` for the on-disk contract.

Every backend honors the same ``Compiler = (source_path, out_dir) -> RenderArtifacts``
contract as OpenSCAD and signals failure the same way — by raising
``render.CompileError`` — so ``evaluate_objective_trial`` records an honest
auto-fail row (objective 0.0) rather than crashing.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from . import render
from .code_cad_objective import Compiler, RenderArtifacts, compile_scad_to_artifacts


SCHEMA = "makerbench-code-cad-backend-job-v1"

#: Backends whose kernel runs Windows-side via the job-dir handshake.
JOB_DIR_BACKENDS = ("fusion", "solidworks")

#: A watcher writes this heartbeat file under ``jobs_root`` while it is alive.
WATCHER_HEARTBEAT = "watcher.heartbeat"

#: Job-dir compile budget (seconds). Kept separate from the OpenSCAD budget: a
#: round-trip through a Windows CAD app (launch + rebuild + export) is far slower
#: than a headless OpenSCAD render, but must still be equal per entrant.
_DEFAULT_BACKEND_TIMEOUT_S = int(os.environ.get("MAKERBENCH_ARENA_BACKEND_TIMEOUT_S", "600"))
_DEFAULT_POLL_INTERVAL_S = float(os.environ.get("MAKERBENCH_ARENA_BACKEND_POLL_S", "2.0"))


def _derive_trial_id(source_path: Path, out_dir: Path) -> str:
    """Stable per-trial id for the job directory.

    The runner passes ``out_dir = <run>/render/<trial_id>``, so the out_dir name
    is the trial id in every real code path; fall back to the source file stem
    when a caller invokes the compiler with a generic out_dir.
    """

    name = out_dir.name
    if name and name not in {".", "..", "render"}:
        return name
    return source_path.stem or "trial"


def _write_status(status_path: Path, payload: dict) -> None:
    """Atomically publish ``status.json`` so a watcher never reads a partial file."""

    tmp = status_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, status_path)


def _read_status(status_path: Path) -> dict:
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A watcher mid-write (non-atomic writer) or a not-yet-created file: the
        # poll loop simply tries again on the next tick.
        return {}


def make_job_dir_compiler(
    jobs_root: Path,
    backend: str,
    *,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    timeout_s: int = _DEFAULT_BACKEND_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Compiler:
    """Build a ``Compiler`` that farms one trial out to a Windows-side watcher.

    For each trial the compiler:

    1. writes the entrant source to ``jobs/<trial_id>/input/<source name>``;
    2. publishes ``jobs/<trial_id>/status.json`` = ``{"state": "pending", ...}``;
    3. polls ``status.json`` until a watcher flips ``state`` to ``"done"`` or
       ``"error"`` (or the per-entrant budget expires);
    4. copies the watcher's ``artifacts/output.stl`` + ``artifacts/preview.png``
       into ``out_dir`` and returns :class:`RenderArtifacts`.

    Failure — watcher error, timeout, or a missing/empty STL — raises
    :class:`render.CompileError`, exactly like the OpenSCAD path, so the trial is
    scored as an honest 0.0 auto-fail instead of crashing the run. ``sleep`` and
    ``clock`` are injectable so a test can play the watcher synchronously.
    """

    jobs_root = Path(jobs_root)

    def compiler(source_path: Path, out_dir: Path) -> RenderArtifacts:
        source_path = Path(source_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        trial_id = _derive_trial_id(source_path, out_dir)
        job_dir = jobs_root / trial_id
        input_dir = job_dir / "input"
        artifacts_dir = job_dir / "artifacts"
        input_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        source_name = source_path.name or "candidate.scad"
        staged_source = input_dir / source_name
        staged_source.write_text(
            source_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )

        status_path = job_dir / "status.json"
        _write_status(
            status_path,
            {
                "schema": SCHEMA,
                "state": "pending",
                "backend": backend,
                "trial_id": trial_id,
                "source": source_name,
                "input": f"input/{source_name}",
                "artifacts": {"stl": "artifacts/output.stl", "preview": "artifacts/preview.png"},
                "timeout_s": timeout_s,
            },
        )

        deadline = clock() + timeout_s
        state = "pending"
        status: dict = {}
        while True:
            status = _read_status(status_path)
            state = str(status.get("state") or "pending")
            if state in {"done", "error"}:
                break
            if clock() >= deadline:
                _write_status(
                    status_path,
                    {**status, "state": "timeout", "backend": backend, "trial_id": trial_id},
                )
                raise render.CompileError(
                    f"{backend} backend timed out after {timeout_s}s waiting on {status_path}"
                )
            sleep(poll_interval_s)

        if state == "error":
            raise render.CompileError(
                f"{backend} backend reported error: {status.get('error') or 'unknown'}"
            )

        exported_stl = artifacts_dir / "output.stl"
        if not exported_stl.exists() or exported_stl.stat().st_size == 0:
            raise render.CompileError(
                f"{backend} backend marked done but produced no STL at {exported_stl}"
            )

        staged_stl = out_dir / "output.stl"
        shutil.copyfile(exported_stl, staged_stl)

        exported_png = artifacts_dir / "preview.png"
        if exported_png.exists() and exported_png.stat().st_size > 0:
            staged_png = out_dir / "preview.png"
            shutil.copyfile(exported_png, staged_png)
        else:
            # A missing preview is non-fatal: the mesh gate scores off the STL;
            # the candidate simply cannot enter blind voting (mirrors the
            # pre-exported-STL ingest precedent).
            staged_png = out_dir / "preview.missing.png"

        warnings = status.get("warnings")
        warnings_tuple = tuple(str(w) for w in warnings) if isinstance(warnings, list) else ()
        return RenderArtifacts(stl_path=staged_stl, png_path=staged_png, warnings=warnings_tuple)

    return compiler


def backend_preflight(backend: str, jobs_root: Path) -> tuple[bool, str]:
    """Readiness check for a backend, analogous to ``render.openscad_available()``.

    OpenSCAD requires its binary. Job-dir backends never do — they require a
    writable ``jobs_root`` (so the handshake can happen) and *report* whether a
    watcher heartbeat is present, without hard-failing on its absence (the
    watcher may be started after the run begins).
    """

    if backend == "openscad":
        if render.openscad_available():
            return True, "openscad binary found"
        return False, "openscad binary not found — objective scoring needs it"

    if backend not in JOB_DIR_BACKENDS:
        return False, f"unknown backend {backend!r} (expected one of {known_backends()})"

    jobs_root = Path(jobs_root)
    try:
        jobs_root.mkdir(parents=True, exist_ok=True)
        probe = jobs_root / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"jobs dir {jobs_root} is not writable: {exc}"

    heartbeat = jobs_root / WATCHER_HEARTBEAT
    if heartbeat.exists():
        return True, f"{backend} jobs dir writable; watcher heartbeat present at {heartbeat}"
    return (
        True,
        f"{backend} jobs dir writable at {jobs_root}; no watcher heartbeat yet "
        f"(start scripts/arena_windows_backend_watcher.ps1 Windows-side)",
    )


#: Compile-backend registry. OpenSCAD delegates to the existing compiler
#: unchanged; the Windows-side kernels resolve to job-dir compilers rooted at the
#: default jobs dir. Callers wanting a custom jobs_root/timeout build their own
#: via :func:`compiler_for_backend`.
BACKEND_COMPILERS: dict[str, Compiler] = {
    "openscad": compile_scad_to_artifacts,
    "fusion": make_job_dir_compiler(Path("runs/code_cad_arena/backend_jobs"), "fusion"),
    "solidworks": make_job_dir_compiler(Path("runs/code_cad_arena/backend_jobs"), "solidworks"),
}


def known_backends() -> tuple[str, ...]:
    return ("openscad", *JOB_DIR_BACKENDS)


def compiler_for_backend(
    name: str,
    *,
    jobs_root: Path | None = None,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    timeout_s: int = _DEFAULT_BACKEND_TIMEOUT_S,
) -> Compiler:
    """Resolve a backend name to its :class:`Compiler`.

    ``"openscad"`` returns the existing ``compile_scad_to_artifacts`` unchanged.
    Job-dir backends build a compiler rooted at ``jobs_root`` (default
    ``runs/code_cad_arena/backend_jobs``) with the given poll/timeout budget.
    """

    if name == "openscad":
        return compile_scad_to_artifacts
    if name in JOB_DIR_BACKENDS:
        root = Path(jobs_root) if jobs_root is not None else Path("runs/code_cad_arena/backend_jobs")
        return make_job_dir_compiler(
            root, name, poll_interval_s=poll_interval_s, timeout_s=timeout_s
        )
    raise ValueError(f"unknown backend {name!r} (expected one of {known_backends()})")


def _null_part_module_counter(_scad_path: Path) -> int:
    """Assembly part-counter for non-OpenSCAD backends: always 0.

    The OpenSCAD-based ``_compile_module_face_count`` re-invokes ``openscad`` to
    count zero-arg part modules — meaningless for a SolidWorks/Fusion source. For
    job-dir backends the assembly check therefore relies solely on the disjoint
    connected-component count of the exported mesh; a mated (fused) assembly is
    not given the standalone-module credit. A backend-native part counter is a
    documented future extension.
    """

    return 0


def gate_factory_for_backend(
    backend: str,
) -> Callable[[dict], Callable] | None:
    """Gate factory override for a backend, or ``None`` to use the arena default.

    For OpenSCAD the default ``mesh_objective_gate`` (which uses the OpenSCAD
    part-module counter) is correct, so this returns ``None``. Job-dir backends
    get a factory that disables the OpenSCAD-only assembly fallback.
    """

    if backend == "openscad":
        return None

    from .code_cad_arena_runner import mesh_objective_gate

    def factory(spec: dict) -> Callable:
        return mesh_objective_gate(spec, part_module_counter=_null_part_module_counter)

    return factory
