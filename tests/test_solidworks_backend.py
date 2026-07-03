"""Tests for the SolidWorks job-dir CAD-backend axis adapter (#627)."""

from __future__ import annotations

import pytest

from makerbench import jobdir_backend
from makerbench import render
from makerbench import solidworks_backend as sw


class TestSolidworksJobdirAvailable:
    def test_delegates_to_shared_jobdir_check(self, monkeypatch):
        monkeypatch.setattr(jobdir_backend, "jobdir_handoff_available", lambda: True)
        assert sw.solidworks_jobdir_available() is True

        monkeypatch.setattr(jobdir_backend, "jobdir_handoff_available", lambda: False)
        assert sw.solidworks_jobdir_available() is False


class TestCompileSolidworksToArtifacts:
    def test_creates_job_polls_and_returns_artifacts(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.vba"
        source.write_text("Sub BuildPart()\nEnd Sub\n", encoding="utf-8")
        out_dir = tmp_path / "render" / "trial-1"

        stl_path = tmp_path / "artifacts" / "output.stl"
        stl_path.parent.mkdir(parents=True)
        stl_path.write_text("solid fake\nendsolid fake\n", encoding="utf-8")
        png_path = tmp_path / "artifacts" / "preview.png"
        png_path.write_bytes(b"png-bytes")

        seen = {}

        def fake_create_job(trial_id, src, *, backend, source_filename=None, root=None):
            seen["trial_id"] = trial_id
            seen["backend"] = backend
            seen["source"] = src
            return tmp_path / "jobs" / trial_id

        def fake_poll_job(job_dir, *, timeout_s, poll_interval_s, sleep_fn, clock_fn):
            seen["job_dir"] = job_dir
            seen["timeout_s"] = timeout_s
            return {
                "status": "done",
                "trial_id": "solidworks-trial-1",
                "backend": "solidworks",
                "stl_path": str(stl_path),
                "png_path": str(png_path),
                "units": "mm",
                "error": None,
            }

        monkeypatch.setattr(jobdir_backend, "create_job", fake_create_job)
        monkeypatch.setattr(jobdir_backend, "poll_job", fake_poll_job)

        artifacts = sw.compile_solidworks_to_artifacts(
            source, out_dir, sleep_fn=lambda s: None, clock_fn=lambda: 0.0
        )

        assert seen["backend"] == "solidworks"
        assert seen["trial_id"] == "solidworks-trial-1"
        assert seen["source"] == source
        assert artifacts.stl_path == stl_path
        assert artifacts.png_path == png_path
        assert artifacts.warnings == ()

    def test_declared_inch_units_are_normalized_to_mm(self, tmp_path, monkeypatch):
        import trimesh

        source = tmp_path / "entrant.vba"
        source.write_text("Sub BuildPart()\nEnd Sub\n", encoding="utf-8")
        out_dir = tmp_path / "render" / "trial-2"

        stl_path = tmp_path / "artifacts" / "output.stl"
        stl_path.parent.mkdir(parents=True)
        mesh = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        mesh.export(stl_path.as_posix())
        original_volume = float(mesh.volume)

        monkeypatch.setattr(
            jobdir_backend, "create_job", lambda *a, **k: tmp_path / "jobs" / "solidworks-trial-2"
        )
        monkeypatch.setattr(
            jobdir_backend,
            "poll_job",
            lambda *a, **k: {
                "status": "done",
                "stl_path": str(stl_path),
                "png_path": None,
                "units": "in",
                "error": None,
            },
        )

        artifacts = sw.compile_solidworks_to_artifacts(
            source, out_dir, sleep_fn=lambda s: None, clock_fn=lambda: 0.0
        )

        assert artifacts.warnings
        assert "in" in artifacts.warnings[0]
        rescaled = trimesh.load(artifacts.stl_path.as_posix(), force="mesh")
        # Volume scales with the cube of the linear scale factor (25.4).
        assert rescaled.volume == pytest.approx(original_volume * (25.4 ** 3), rel=1e-3)

    def test_empty_stl_is_compile_error(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.vba"
        source.write_text("Sub BuildPart()\nEnd Sub\n", encoding="utf-8")
        stl_path = tmp_path / "artifacts" / "output.stl"
        stl_path.parent.mkdir(parents=True)
        stl_path.write_text("", encoding="utf-8")

        monkeypatch.setattr(jobdir_backend, "create_job", lambda *a, **k: tmp_path / "jobs" / "j")
        monkeypatch.setattr(
            jobdir_backend,
            "poll_job",
            lambda *a, **k: {"status": "done", "stl_path": str(stl_path), "png_path": None, "units": None},
        )

        with pytest.raises(render.CompileError, match="empty STL"):
            sw.compile_solidworks_to_artifacts(
                source, tmp_path / "out", sleep_fn=lambda s: None, clock_fn=lambda: 0.0
            )

    def test_poll_job_error_propagates_as_compile_error(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.vba"
        source.write_text("Sub BuildPart()\nEnd Sub\n", encoding="utf-8")

        monkeypatch.setattr(jobdir_backend, "create_job", lambda *a, **k: tmp_path / "jobs" / "j")

        def fake_poll_job(*a, **k):
            raise render.CompileError("Windows-side runner reported an error: bad macro")

        monkeypatch.setattr(jobdir_backend, "poll_job", fake_poll_job)

        with pytest.raises(render.CompileError, match="bad macro"):
            sw.compile_solidworks_to_artifacts(
                source, tmp_path / "out", sleep_fn=lambda s: None, clock_fn=lambda: 0.0
            )
