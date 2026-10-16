"""Deterministic, confined STEP intake into mm STL meshes (optional OCP)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from . import cadquery_backend as cq
from .render import CompileError

MAX_INPUT_BYTES = 50 * 1024**2
TIMEOUT_S = 120


@dataclass(frozen=True)
class StepMeshArtifacts:
    stl_path: Path
    body_paths: tuple[Path, ...]
    manifest_path: Path
    backend: str = "step"


def convert_step_to_mesh(source: Path, out_dir: Path) -> StepMeshArtifacts:
    """Convert untrusted STEP without executing entrant code or exposing the repo.

    Candidate failures raise CompileError; unavailable confinement/kernel raises
    RuntimeError. No scorelines are created or blended by this intake helper.
    """
    try:
        source = Path(source).resolve(strict=True)
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_INPUT_BYTES:
            raise CompileError("STEP input must be a nonempty file of at most 50 MiB")
    except OSError as exc:
        raise CompileError("STEP input cannot be read") from exc
    env = cq._scrub_environment(os.environ)
    if not cq._bubblewrap_available(env):
        raise RuntimeError("STEP filesystem sandbox unavailable: install bubblewrap")
    with tempfile.TemporaryDirectory(prefix="makerbench-step-") as tmp:
        scratch = Path(tmp)
        staged = scratch / "input.step"
        with source.open("rb") as stream:
            content = stream.read(MAX_INPUT_BYTES + 1)
        if len(content) > MAX_INPUT_BYTES:
            raise CompileError("STEP input exceeds 50 MiB limit")
        staged.write_bytes(content)
        worker_out = scratch / "out"
        worker_out.mkdir()
        command = cq._bubblewrap_command(
            driver_path=Path(__file__).with_name("step_mesh_worker.py"),
            script_path=staged, out_dir=worker_out,
        )
        try:
            # Native STEP diagnostics can be noisy. Spool them to bounded files
            # (the worker's file-size rlimit applies), not unbounded host RAM.
            with (scratch / "stdout").open("w+b") as stdout, (scratch / "stderr").open("w+b") as stderr:
                proc = subprocess.run(command, stdout=stdout, stderr=stderr,
                                      timeout=TIMEOUT_S, cwd=tmp, env=env, check=False)
                stdout.seek(max(0, stdout.tell() - 8192))
                diagnostics = stdout.read().decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise CompileError(f"STEP conversion timed out after {TIMEOUT_S}s") from exc
        if proc.returncode == 20 and "STEP_ENVIRONMENT_ERROR:" in diagnostics:
            raise RuntimeError("STEP requires the pinned requirements-step.lock environment")
        if proc.returncode or "STEP_OK" not in diagnostics.splitlines():
            # Never pass native parser diagnostics or tracebacks to users.
            errors = [line for line in diagnostics.splitlines() if line.startswith("STEP_ERROR:")]
            raise CompileError(errors[-1] if errors else "STEP conversion failed within resource limits")
        manifest = json.loads((worker_out / "bodies.json").read_text())
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        names = ["output.stl", "bodies.json", *(body["path"] for body in manifest["bodies"])]
        for name in names:
            shutil.copyfile(worker_out / name, out_dir / name)
    return StepMeshArtifacts(out_dir / "output.stl",
                             tuple(out_dir / body["path"] for body in manifest["bodies"]),
                             out_dir / "bodies.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    try:
        result = convert_step_to_mesh(args.source, args.out_dir)
    except (CompileError, RuntimeError, OSError) as exc:
        parser.exit(1, f"STEP: {exc}\n")
    print(result.stl_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
