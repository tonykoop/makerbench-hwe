"""Tests for the Fusion 360 job-dir CAD-backend axis adapter (#627)."""

from __future__ import annotations

import pytest

from makerbench import fusion_backend as fusion
from makerbench import jobdir_backend
from makerbench import render


class TestFusionJobdirAvailable:
    def test_delegates_to_shared_jobdir_check(self, monkeypatch):
        monkeypatch.setattr(jobdir_backend, "jobdir_handoff_available", lambda: True)
        assert fusion.fusion_jobdir_available() is True

        monkeypatch.setattr(jobdir_backend, "jobdir_handoff_available", lambda: False)
        assert fusion.fusion_jobdir_available() is False


class TestCompileFusionToArtifacts:
    def test_creates_job_polls_and_returns_artifacts(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.py"
        source.write_text("def build(app, design):\n    pass\n", encoding="utf-8")
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
            return tmp_path / "jobs" / trial_id

        def fake_poll_job(job_dir, *, timeout_s, poll_interval_s, sleep_fn, clock_fn):
            seen["job_dir"] = job_dir
            return {
                "status": "done",
                "trial_id": "fusion-trial-1",
                "backend": "fusion",
                "stl_path": str(stl_path),
                "png_path": str(png_path),
                "units": "mm",
                "error": None,
            }

        monkeypatch.setattr(jobdir_backend, "create_job", fake_create_job)
        monkeypatch.setattr(jobdir_backend, "poll_job", fake_poll_job)

        artifacts = fusion.compile_fusion_to_artifacts(
            source, out_dir, sleep_fn=lambda s: None, clock_fn=lambda: 0.0
        )

        assert seen["backend"] == "fusion"
        assert seen["trial_id"] == "fusion-trial-1"
        assert artifacts.stl_path == stl_path
        assert artifacts.png_path == png_path

    def test_missing_png_falls_back_to_placeholder_path(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.py"
        source.write_text("def build(app, design):\n    pass\n", encoding="utf-8")
        out_dir = tmp_path / "render" / "trial-2"
        stl_path = tmp_path / "artifacts" / "output.stl"
        stl_path.parent.mkdir(parents=True)
        stl_path.write_text("solid fake\nendsolid fake\n", encoding="utf-8")

        monkeypatch.setattr(jobdir_backend, "create_job", lambda *a, **k: tmp_path / "jobs" / "j")
        monkeypatch.setattr(
            jobdir_backend,
            "poll_job",
            lambda *a, **k: {"status": "done", "stl_path": str(stl_path), "png_path": None},
        )

        artifacts = fusion.compile_fusion_to_artifacts(
            source, out_dir, sleep_fn=lambda s: None, clock_fn=lambda: 0.0
        )
        assert artifacts.png_path == out_dir / "preview.missing.png"

    def test_empty_stl_is_compile_error(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.py"
        source.write_text("def build(app, design):\n    pass\n", encoding="utf-8")
        stl_path = tmp_path / "artifacts" / "output.stl"
        stl_path.parent.mkdir(parents=True)
        stl_path.write_text("", encoding="utf-8")

        monkeypatch.setattr(jobdir_backend, "create_job", lambda *a, **k: tmp_path / "jobs" / "j")
        monkeypatch.setattr(
            jobdir_backend,
            "poll_job",
            lambda *a, **k: {"status": "done", "stl_path": str(stl_path), "png_path": None},
        )

        with pytest.raises(render.CompileError, match="empty STL"):
            fusion.compile_fusion_to_artifacts(
                source, tmp_path / "out", sleep_fn=lambda s: None, clock_fn=lambda: 0.0
            )

    def test_poll_job_timeout_propagates_as_compile_error(self, tmp_path, monkeypatch):
        source = tmp_path / "entrant.py"
        source.write_text("def build(app, design):\n    pass\n", encoding="utf-8")

        monkeypatch.setattr(jobdir_backend, "create_job", lambda *a, **k: tmp_path / "jobs" / "j")

        def fake_poll_job(*a, **k):
            raise render.CompileError("Windows-side runner did not complete within 300s")

        monkeypatch.setattr(jobdir_backend, "poll_job", fake_poll_job)

        with pytest.raises(render.CompileError, match="did not complete within"):
            fusion.compile_fusion_to_artifacts(
                source, tmp_path / "out", sleep_fn=lambda s: None, clock_fn=lambda: 0.0
            )
