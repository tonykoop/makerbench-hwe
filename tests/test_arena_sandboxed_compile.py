"""``arena run --sandboxed-compile`` (#788 Q11): opt-in, fails closed, and
never starts a host ``openscad`` process when on.

The end-to-end test runs the real stub matrix through the real Bubblewrap
wrapper; it is skipped only when the sandbox is unavailable, and in CI
``MAKERBENCH_REQUIRE_SANDBOX=1`` turns that skip into a failure.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from makerbench import cli_arena, render, scad_sandbox
from makerbench import code_cad_arena_runner as arena_runner
from makerbench.cli import app
from makerbench.code_cad_objective import compile_scad_to_artifacts
from makerbench.code_cad_orchestrator import OrchestrationConfig

runner = CliRunner()

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()
if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start", pytrace=False)
needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

TINY_REGISTRY = {
    "schema": "makerbench-code-cad-arena-registry-v1",
    "instruments": [
        {
            "id": "boxolin",
            "display_name": "Boxolin",
            "family": "strings",
            "task_kind": "single_part",
            "task_brief": "a box",
            "constraints": {},
            "envelope_mm": [100, 100, 100],
            "min_bodies": 1,
        }
    ],
}


def _args(run_dir: Path, registry: Path, *extra: str) -> list[str]:
    return [
        "arena", "run",
        "--run-dir", run_dir.as_posix(),
        "--registry", registry.as_posix(),
        "--instruments", "boxolin",
        "--models", "stub-a,stub-b",
        "--stub",
        "--rate-limit-s", "0",
        *extra,
    ]


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(TINY_REGISTRY), encoding="utf-8")
    return path


def _capture_compiler(monkeypatch):
    """Short-circuit the run after wiring: record the compiler ``arena run``
    hands to ``make_execute_trial`` and the config it orchestrates with."""

    seen: dict = {}

    def fake_make_execute_trial(**kwargs):
        seen["compiler"] = kwargs["compiler"]
        return lambda trial: {}

    def fake_run_orchestration(*, config, run_log_path, execute_trial, **kwargs):
        seen["config"] = config
        return {"schema": "x", "config": config.as_dict(), "trials": [], "summary": {"counts": {}}}

    monkeypatch.setattr(arena_runner, "make_execute_trial", fake_make_execute_trial)
    monkeypatch.setattr(cli_arena, "run_orchestration", fake_run_orchestration)
    monkeypatch.setattr(arena_runner, "collect_objective_scoreline", lambda log: [])
    monkeypatch.setattr(cli_arena, "_print_objective_scoreline", lambda rows: None, raising=False)
    return seen


class TestWiring:
    def test_default_is_the_host_compiler_and_records_false(self, tmp_path, registry, monkeypatch):
        seen = _capture_compiler(monkeypatch)
        result = runner.invoke(app, _args(tmp_path / "run", registry))
        assert result.exit_code == 0, result.stdout
        assert seen["compiler"] is compile_scad_to_artifacts
        assert seen["config"].compile_sandboxed is False
        assert seen["config"].as_dict()["compile_sandboxed"] is False

    def test_flag_selects_the_sandboxed_compiler_and_records_true(self, tmp_path, registry, monkeypatch):
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
        seen = _capture_compiler(monkeypatch)
        result = runner.invoke(app, _args(tmp_path / "run", registry, "--sandboxed-compile"))
        assert result.exit_code == 0, result.stdout
        assert seen["compiler"] is scad_sandbox.compile_scad_sandboxed
        assert seen["config"].compile_sandboxed is True
        assert seen["config"].as_dict()["compile_sandboxed"] is True
        assert "sandboxed compile" in result.stdout

    def test_config_dict_round_trip_carries_the_field(self):
        config = OrchestrationConfig(instrument_ids=("a",), model_ids=("m",), seeds=(0,), compile_sandboxed=True)
        assert config.as_dict()["compile_sandboxed"] is True
        assert OrchestrationConfig(instrument_ids=("a",), model_ids=("m",), seeds=(0,)).as_dict()["compile_sandboxed"] is False


class TestFailClosed:
    def test_unavailable_sandbox_stops_before_any_trial(self, tmp_path, registry, monkeypatch):
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
        launched: list = []
        monkeypatch.setattr(render, "_run", lambda *a, **k: launched.append(a))
        monkeypatch.setattr(scad_sandbox.subprocess, "run", lambda *a, **k: launched.append(a))
        monkeypatch.setattr(cli_arena, "run_orchestration", lambda **k: launched.append("orchestrated"))
        result = runner.invoke(app, _args(tmp_path / "run", registry, "--sandboxed-compile"))
        assert result.exit_code == 1
        flat = " ".join(result.stdout.split())
        assert "sandbox cannot start" in flat
        assert "Not falling back to the host compiler" in flat
        assert launched == []
        assert not (tmp_path / "run" / "run_log.json").exists()

    @pytest.mark.parametrize("backend", ["blender", "solidworks", "fusion"])
    def test_backends_without_a_sandboxed_compiler_are_refused(self, tmp_path, registry, monkeypatch, backend):
        # Skip the per-backend binary/job-dir preflights so the refusal is
        # the sandboxed-compile check itself.
        from makerbench import blender_backend, fusion_backend, solidworks_backend

        monkeypatch.setattr(blender_backend, "blender_available", lambda: True)
        monkeypatch.setattr(solidworks_backend, "solidworks_jobdir_available", lambda: True)
        monkeypatch.setattr(fusion_backend, "fusion_jobdir_available", lambda: True)
        monkeypatch.setattr(cli_arena, "run_orchestration", lambda **k: pytest.fail("ran"))
        result = runner.invoke(
            app, _args(tmp_path / "run", registry, "--sandboxed-compile", "--backend", backend)
        )
        assert result.exit_code == 1, result.stdout
        assert "no sandboxed compiler" in result.stdout

    def test_parametric_backend_is_refused(self, tmp_path, registry, monkeypatch):
        monkeypatch.setattr(cli_arena, "run_orchestration", lambda **k: pytest.fail("ran"))
        result = runner.invoke(
            app, _args(tmp_path / "run", registry, "--sandboxed-compile", "--backend", "parametric")
        )
        assert result.exit_code == 1, result.stdout
        assert "does not apply" in result.stdout


@needs_sandbox
class TestRealStubRun:
    def test_no_host_openscad_process_starts_when_the_flag_is_on(self, tmp_path, registry, monkeypatch):
        # Every process launch is observed; ``openscad`` may only ever appear
        # behind ``bwrap``. The host path (``render._run``) must not run.
        argvs: list[list[str]] = []
        real_run = subprocess.run

        def spy_run(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)):
                argvs.append([str(c) for c in cmd])
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy_run)
        monkeypatch.setattr(
            render, "_run", lambda *a, **k: pytest.fail("host openscad path was used with --sandboxed-compile")
        )
        run_dir = tmp_path / "run"
        result = runner.invoke(app, _args(run_dir, registry, "--sandboxed-compile"))
        assert result.exit_code == 0, result.stdout
        log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        assert log["config"]["compile_sandboxed"] is True
        assert log["summary"]["counts"] == {"scored": 2}, log["summary"]
        openscad_launches = [argv for argv in argvs if any(Path(c).name == "openscad" for c in argv)]
        assert openscad_launches, "no compile happened at all"
        for argv in openscad_launches:
            assert Path(argv[0]).name == "bwrap", argv[:3]
            assert "--unshare-net" in argv and "--clearenv" in argv
        # a real sandboxed mesh and preview landed for each trial
        render_dirs = sorted((run_dir / "render").iterdir())
        assert len(render_dirs) == 2
        for rendered in render_dirs:
            assert (rendered / "output.stl").stat().st_size > 0
            assert (rendered / "preview.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_without_the_flag_the_host_compiler_runs(self, tmp_path, registry, monkeypatch):
        calls: list = []
        real_run = render._run

        def spy(*a, **k):
            calls.append(a)
            return real_run(*a, **k)

        monkeypatch.setattr(render, "_run", spy)
        result = runner.invoke(app, _args(tmp_path / "run", registry))
        assert result.exit_code == 0, result.stdout
        assert calls, "host openscad path did not run without the flag"
        log = json.loads((tmp_path / "run" / "run_log.json").read_text(encoding="utf-8"))
        assert log["config"]["compile_sandboxed"] is False
