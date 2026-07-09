"""Tests for the Code-CAD Arena compile-backend axis (#627).

The job-dir handshake is exercised with a FAKE watcher that plays the Windows
side synchronously — writing a real STL + PNG and flipping ``status.json`` to
``done`` on the first poll — so a trial runs end to end through the SolidWorks/
Fusion seam with no real CAD app, COM, or Windows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import code_cad_backends as backends
from makerbench import render
from makerbench.code_cad_objective import RenderArtifacts, compile_scad_to_artifacts
from makerbench.code_cad_orchestrator import OrchestrationConfig, run_orchestration
from makerbench.code_cad_providers import make_stub_generator


def _fake_watcher(jobs_root: Path, *, export: bool = True, error: str | None = None):
    """A synchronous stand-in for the Windows watcher.

    Returned as a ``sleep`` callback: every time the poll loop would sleep, the
    watcher instead services the one pending job — exporting a tiny valid STL +
    PNG and flipping ``status.json`` to ``done`` (or ``error``). This makes the
    poll loop terminate on its next read with no real wall-clock wait.
    """

    def watcher(_interval: float) -> None:
        for status_path in jobs_root.glob("*/status.json"):
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("state") != "pending":
                continue
            job_dir = status_path.parent
            artifacts_dir = job_dir / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            if error is not None:
                status["state"] = "error"
                status["error"] = error
            else:
                if export:
                    trimesh.creation.box(extents=[30, 20, 10]).export(
                        (artifacts_dir / "output.stl").as_posix()
                    )
                    (artifacts_dir / "preview.png").write_bytes(b"\x89PNG\r\n")
                status["state"] = "done"
            status_path.write_text(json.dumps(status), encoding="utf-8")

    return watcher


class TestBackendRegistry:
    def test_openscad_backend_returns_existing_compiler(self):
        assert backends.compiler_for_backend("openscad") is compile_scad_to_artifacts
        assert backends.BACKEND_COMPILERS["openscad"] is compile_scad_to_artifacts

    def test_known_backends_cover_the_axis(self):
        assert set(backends.known_backends()) == {"openscad", "fusion", "solidworks"}

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown backend"):
            backends.compiler_for_backend("catia")

    def test_openscad_gate_factory_is_default(self):
        # None means "use the arena's default mesh_objective_gate unchanged".
        assert backends.gate_factory_for_backend("openscad") is None

    def test_job_dir_gate_factory_disables_openscad_part_counter(self, tmp_path):
        factory = backends.gate_factory_for_backend("fusion")
        assert factory is not None
        # A single fused body under an assembly spec must NOT be rescued by the
        # OpenSCAD module counter (which would re-invoke openscad on a non-scad
        # source); the job-dir gate uses the null counter → body_count fails.
        gate = factory({"id": "kora", "envelope_mm": [1500, 700, 700],
                        "min_bodies": 4, "assembly": True})
        stl = tmp_path / "output.stl"
        trimesh.creation.box(extents=[50, 50, 50]).export(stl.as_posix())
        png = tmp_path / "preview.png"
        png.write_bytes(b"\x89PNG\r\n")
        from makerbench.code_cad_objective import ObjectiveContext
        ctx = ObjectiveContext(
            trial_id="t", model_id="m", instrument_id="kora", seed=0,
            scad_path=tmp_path / "in.scad",
            artifacts=RenderArtifacts(stl_path=stl, png_path=png),
        )
        result = gate(ctx)
        assert result["sub_scores"]["body_count"] == 0.0
        assert result["metrics"]["part_modules_compiled"] == 0


class TestBackendPreflight:
    def test_job_dir_preflight_ok_when_writable(self, tmp_path):
        ok, detail = backends.backend_preflight("fusion", tmp_path / "jobs")
        assert ok is True
        assert "no watcher heartbeat" in detail

    def test_job_dir_preflight_reports_heartbeat(self, tmp_path):
        jobs = tmp_path / "jobs"
        jobs.mkdir()
        (jobs / backends.WATCHER_HEARTBEAT).write_text("alive", encoding="utf-8")
        ok, detail = backends.backend_preflight("solidworks", jobs)
        assert ok is True
        assert "heartbeat present" in detail

    def test_unknown_backend_preflight_fails(self, tmp_path):
        ok, detail = backends.backend_preflight("catia", tmp_path)
        assert ok is False
        assert "unknown backend" in detail

    def test_openscad_preflight_does_not_touch_jobs_dir(self, tmp_path):
        # Whatever the local openscad state, the message is about the binary,
        # never the jobs dir.
        ok, detail = backends.backend_preflight("openscad", tmp_path / "unused")
        assert "openscad binary" in detail
        assert ok == render.openscad_available()


class TestJobDirCompiler:
    def test_compiles_end_to_end_via_fake_watcher(self, tmp_path):
        jobs_root = tmp_path / "jobs"
        compiler = backends.make_job_dir_compiler(
            jobs_root, "fusion", sleep=_fake_watcher(jobs_root)
        )
        source = tmp_path / "candidate.scad"
        source.write_text("// solidworks/fusion feature tree placeholder\n", encoding="utf-8")
        out_dir = tmp_path / "render" / "boxolin__seed0__rep0__adam-fusion"
        artifacts = compiler(source, out_dir)

        assert artifacts.stl_path.exists()
        assert artifacts.stl_path == out_dir / "output.stl"
        assert artifacts.png_path == out_dir / "preview.png"
        # The exported mesh is a real, loadable solid.
        mesh = trimesh.load(artifacts.stl_path.as_posix(), force="mesh")
        assert len(mesh.faces) > 0
        # Job status ended up marked done.
        status = json.loads((jobs_root / out_dir.name / "status.json").read_text())
        assert status["state"] == "done"
        assert status["backend"] == "fusion"

    def test_missing_preview_is_nonfatal(self, tmp_path):
        jobs_root = tmp_path / "jobs"
        watcher = _fake_watcher(jobs_root)

        def no_png_watcher(interval: float) -> None:
            watcher(interval)
            for png in jobs_root.glob("*/artifacts/preview.png"):
                png.unlink()

        compiler = backends.make_job_dir_compiler(
            jobs_root, "solidworks", sleep=no_png_watcher
        )
        source = tmp_path / "candidate.scad"
        source.write_text("x\n", encoding="utf-8")
        out_dir = tmp_path / "render" / "t1"
        # First poll: watcher exports STL + PNG then we delete the PNG; the STL
        # still lets the compile succeed.
        artifacts = compiler(source, out_dir)
        assert artifacts.stl_path.exists()
        assert artifacts.png_path.name == "preview.missing.png"

    def test_watcher_error_raises_compile_error(self, tmp_path):
        jobs_root = tmp_path / "jobs"
        compiler = backends.make_job_dir_compiler(
            jobs_root, "fusion", sleep=_fake_watcher(jobs_root, error="rebuild failed")
        )
        source = tmp_path / "candidate.scad"
        source.write_text("x\n", encoding="utf-8")
        with pytest.raises(render.CompileError, match="rebuild failed"):
            compiler(source, tmp_path / "render" / "t1")

    def test_done_without_stl_raises_compile_error(self, tmp_path):
        jobs_root = tmp_path / "jobs"
        # export=False: watcher flips to done but exports nothing.
        compiler = backends.make_job_dir_compiler(
            jobs_root, "fusion", sleep=_fake_watcher(jobs_root, export=False)
        )
        source = tmp_path / "candidate.scad"
        source.write_text("x\n", encoding="utf-8")
        with pytest.raises(render.CompileError, match="no STL"):
            compiler(source, tmp_path / "render" / "t1")

    def test_timeout_raises_compile_error(self, tmp_path):
        jobs_root = tmp_path / "jobs"
        # A clock that jumps past the deadline on the first check, and a sleep
        # that never services the job → timeout branch.
        ticks = iter([0.0, 0.0, 1000.0])

        compiler = backends.make_job_dir_compiler(
            jobs_root, "fusion", timeout_s=5,
            sleep=lambda _i: None, clock=lambda: next(ticks),
        )
        source = tmp_path / "candidate.scad"
        source.write_text("x\n", encoding="utf-8")
        with pytest.raises(render.CompileError, match="timed out"):
            compiler(source, tmp_path / "render" / "t1")


TINY_REGISTRY = {
    "instruments": [
        {
            "id": "boxolin",
            "task_brief": "a box instrument",
            "envelope_mm": [100, 100, 100],
            "min_bodies": 1,
        }
    ]
}


class TestJobDirBackendThroughOrchestrator:
    def test_full_trial_runs_through_the_job_dir_seam(self, tmp_path):
        """A stub-generated candidate scores end to end via the fake watcher."""

        jobs_root = tmp_path / "jobs"
        compiler = backends.make_job_dir_compiler(
            jobs_root, "fusion", sleep=_fake_watcher(jobs_root)
        )
        config = OrchestrationConfig(
            instrument_ids=("boxolin",), model_ids=("adam-fusion",), seeds=(0,), reps=1,
        )
        execute = runner.make_execute_trial(
            registry=TINY_REGISTRY, run_dir=tmp_path,
            generators={"adam-fusion": make_stub_generator()},
            compiler=compiler,
            gate_factory=backends.gate_factory_for_backend("fusion"),
        )
        log = run_orchestration(
            config=config, run_log_path=tmp_path / "run_log.json", execute_trial=execute,
        )
        assert log["summary"]["counts"] == {"scored": 1}
        rows = runner.collect_objective_scoreline(log)
        assert rows[0]["entrant"] == "adam-fusion"
        assert rows[0]["objective_pass_rate"] == 1.0

    def test_backend_error_is_scored_as_honest_zero(self, tmp_path):
        """A watcher error becomes an auto-fail row (0.0), never a crash."""

        jobs_root = tmp_path / "jobs"
        compiler = backends.make_job_dir_compiler(
            jobs_root, "fusion", sleep=_fake_watcher(jobs_root, error="kernel crash")
        )
        config = OrchestrationConfig(
            instrument_ids=("boxolin",), model_ids=("adam-fusion",), seeds=(0,), reps=1,
            max_attempts=1,
        )
        execute = runner.make_execute_trial(
            registry=TINY_REGISTRY, run_dir=tmp_path,
            generators={"adam-fusion": make_stub_generator()},
            compiler=compiler,
            gate_factory=backends.gate_factory_for_backend("fusion"),
        )
        log = run_orchestration(
            config=config, run_log_path=tmp_path / "run_log.json", execute_trial=execute,
        )
        entry = log["trials"][0]
        assert entry["status"] == "auto_fail"
        assert entry["result"]["objective"]["objective_pass_rate"] == 0.0
        assert entry["result"]["render_ok"] is False


class TestOpenScadPathUnchanged:
    def test_openscad_compiler_identity_is_the_regression_guard(self):
        # compiler_for_backend must hand back the *exact* existing function so
        # the OpenSCAD scoring path is provably untouched by the new axis.
        assert backends.compiler_for_backend("openscad") is compile_scad_to_artifacts

    def test_ingest_pre_exported_stl_still_works_with_default_backend(self, tmp_path):
        # Regression: the pre-exported-STL ingest path (which overrides the
        # compiler) is unaffected — the backend default doesn't disturb it.
        config = OrchestrationConfig(
            instrument_ids=("boxolin",), model_ids=("stub-a",), seeds=(0,), reps=1,
        )

        def _fake_compiler(scad_path: Path, out_dir: Path) -> RenderArtifacts:
            out_dir.mkdir(parents=True, exist_ok=True)
            stl = out_dir / "output.stl"
            trimesh.creation.box(extents=[30, 20, 10]).export(stl.as_posix())
            png = out_dir / "preview.png"
            png.write_bytes(b"\x89PNG\r\n")
            return RenderArtifacts(stl_path=stl, png_path=png)

        execute = runner.make_execute_trial(
            registry=TINY_REGISTRY, run_dir=tmp_path,
            generators={"stub-a": make_stub_generator()}, compiler=_fake_compiler,
        )
        run_orchestration(config=config, run_log_path=tmp_path / "run_log.json",
                          execute_trial=execute)
        stl = tmp_path / "ext.stl"
        trimesh.creation.box(extents=[30, 20, 10]).export(stl.as_posix())
        png = tmp_path / "ext.png"
        png.write_bytes(b"\x89PNG\r\n")
        scad = tmp_path / "ext.scad"
        scad.write_text("// solidworks export placeholder\n", encoding="utf-8")
        entry = runner.ingest_candidate(
            run_log_path=tmp_path / "run_log.json", registry=TINY_REGISTRY,
            instrument_id="boxolin", model_id="adam-solidworks",
            scad_path=scad, run_dir=tmp_path,
            stl_path=stl, png_path=png,
            compiler=backends.compiler_for_backend("openscad"),
        )
        assert entry["status"] == "scored"
        assert entry["result"]["objective"]["objective_pass_rate"] == 1.0
