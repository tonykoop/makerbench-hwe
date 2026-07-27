"""Tests for the Blender bpy CAD-backend axis compiler (#601)."""

from __future__ import annotations

import subprocess

import pytest

from makerbench import blender_backend
from makerbench import render


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestBlenderAvailable:
    def test_reports_true_when_binary_on_path(self, monkeypatch):
        monkeypatch.setattr(blender_backend.shutil, "which", lambda _bin: "/usr/bin/blender")
        assert blender_backend.blender_available() is True

    def test_reports_false_when_binary_missing(self, monkeypatch):
        monkeypatch.setattr(blender_backend.shutil, "which", lambda _bin: None)
        assert blender_backend.blender_available() is False


class TestCompileBpyToArtifactsMocked:
    """Exercise the subprocess contract without needing a real Blender binary."""

    def test_success_returns_render_artifacts(self, tmp_path, monkeypatch):
        script = tmp_path / "entrant.py"
        script.write_text("bpy.ops.mesh.primitive_cube_add(size=10)\n", encoding="utf-8")
        out_dir = tmp_path / "out"

        def fake_run(cmd, **kwargs):
            # Simulate the driver producing STL/PNG artifacts at the paths
            # the compiler passed after "--".
            idx = cmd.index("--")
            _entrant, stl_out, png_out = cmd[idx + 1 :]
            from pathlib import Path

            Path(stl_out).write_text("solid fake\nendsolid fake\n", encoding="utf-8")
            Path(png_out).write_bytes(b"png-bytes")
            return _completed(stdout="BLENDER_DRIVER_OK\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        artifacts = blender_backend.compile_bpy_to_artifacts(script, out_dir)
        assert artifacts.stl_path.exists()
        assert artifacts.png_path.exists()
        assert artifacts.stl_path.read_text().startswith("solid fake")

    def test_driver_error_becomes_compile_error(self, tmp_path, monkeypatch):
        script = tmp_path / "entrant.py"
        script.write_text("raise ValueError('bad script')\n", encoding="utf-8")

        def fake_run(cmd, **kwargs):
            return _completed(
                returncode=3,
                stdout="BLENDER_DRIVER_ERROR: entrant script raised: ValueError('bad script')\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(render.CompileError, match="entrant script raised"):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")

    def test_missing_stl_is_compile_error(self, tmp_path, monkeypatch):
        script = tmp_path / "entrant.py"
        script.write_text("pass\n", encoding="utf-8")

        def fake_run(cmd, **kwargs):
            # Reports OK but never actually wrote output.stl.
            return _completed(stdout="BLENDER_DRIVER_OK\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(render.CompileError):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")

    def test_empty_stl_is_compile_error(self, tmp_path, monkeypatch):
        script = tmp_path / "entrant.py"
        script.write_text("pass\n", encoding="utf-8")

        def fake_run(cmd, **kwargs):
            idx = cmd.index("--")
            _entrant, stl_out, png_out = cmd[idx + 1 :]
            from pathlib import Path

            Path(stl_out).write_text("", encoding="utf-8")
            Path(png_out).write_bytes(b"png")
            return _completed(stdout="BLENDER_DRIVER_OK\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(render.CompileError, match="empty STL"):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")

    def test_timeout_becomes_compile_error(self, tmp_path, monkeypatch):
        script = tmp_path / "entrant.py"
        script.write_text("pass\n", encoding="utf-8")

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(render.CompileError, match="timed out"):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")

    def test_timeout_env_override(self, tmp_path, monkeypatch):
        script = tmp_path / "entrant.py"
        script.write_text("pass\n", encoding="utf-8")
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setenv("MAKERBENCH_BLENDER_TIMEOUT_S", "42")
        with pytest.raises(render.CompileError):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")
        assert seen["timeout"] == 42


@pytest.mark.skipif(not blender_backend.blender_available(), reason="blender binary not on PATH")
class TestCompileBpyToArtifactsRealBlender:
    """End-to-end smoke test against the real headless Blender binary (#601 item 2)."""

    def test_minimal_cube_script_compiles_and_renders(self, tmp_path):
        script = tmp_path / "entrant.py"
        script.write_text(
            "bpy.ops.mesh.primitive_cube_add(size=20)\n",
            encoding="utf-8",
        )
        artifacts = blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")

        assert artifacts.stl_path.exists()
        assert artifacts.stl_path.stat().st_size > 0
        assert artifacts.png_path.exists()
        assert artifacts.png_path.stat().st_size > 0

        import trimesh

        mesh = trimesh.load(artifacts.stl_path.as_posix(), force="mesh")
        assert len(mesh.faces) > 0
        assert abs(float(mesh.volume)) > 0

    def test_bad_entrant_script_is_compile_error(self, tmp_path):
        script = tmp_path / "entrant.py"
        script.write_text("raise ValueError('deliberately broken')\n", encoding="utf-8")

        with pytest.raises(render.CompileError, match="deliberately broken"):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")

    def test_script_with_no_geometry_is_compile_error(self, tmp_path):
        script = tmp_path / "entrant.py"
        script.write_text("pass\n", encoding="utf-8")

        with pytest.raises(render.CompileError, match="no MESH objects"):
            blender_backend.compile_bpy_to_artifacts(script, tmp_path / "out")
