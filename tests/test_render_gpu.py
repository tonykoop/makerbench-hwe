"""Unit tests for headless GPU render driver & fallback (Story #695)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from makerbench import render


def test_detect_egl_context_disabled_by_cuda_visible_devices(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert render.detect_egl_context() is False

    can_gpu, reason = render.gpu_render_available()
    assert can_gpu is False
    assert reason == "cuda_disabled"


def test_detect_egl_context_detected_via_dri(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/dev/dri/renderD128")
    assert render.detect_egl_context() is True


def test_render_turntable_fallback_to_openscad(tmp_path: Path, monkeypatch):
    """When GPU is not available, render_turntable falls back cleanly to OpenSCAD."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")

    # Mock _render_turntable_openscad
    fake_frames = [str(tmp_path / f"frame_{i:02d}.png") for i in range(4)]
    for f in fake_frames:
        Path(f).write_text("fake_png", encoding="utf-8")

    mock_openscad = MagicMock(return_value=fake_frames)
    monkeypatch.setattr(render, "_render_turntable_openscad", mock_openscad)

    mesh_file = tmp_path / "model.stl"
    mesh_file.write_text("solid test\nendsolid", encoding="utf-8")

    result = render.render_turntable(
        str(mesh_file), str(tmp_path / "frames"), frames=4, prefer_gpu=True
    )
    assert result == fake_frames
    assert mock_openscad.called


def test_render_turntable_gpu_failure_falls_back_seamlessly(tmp_path: Path, monkeypatch):
    """If render_turntable_gpu throws an error, fallback succeeds without raising."""
    monkeypatch.setattr(render, "gpu_render_available", lambda: (True, "mock_gpu"))

    def failing_gpu(*args, **kwargs):
        raise RuntimeError("Simulated EGL / Blender driver crash")

    fake_frames = [str(tmp_path / f"frame_{i:02d}.png") for i in range(2)]
    for f in fake_frames:
        Path(f).write_text("fake_png", encoding="utf-8")

    monkeypatch.setattr(render, "render_turntable_gpu", failing_gpu)
    monkeypatch.setattr(render, "_render_turntable_openscad", MagicMock(return_value=fake_frames))

    mesh_file = tmp_path / "model.stl"
    mesh_file.write_text("solid test\nendsolid", encoding="utf-8")

    result = render.render_turntable(
        str(mesh_file), str(tmp_path / "frames"), frames=2, prefer_gpu=True
    )
    assert result == fake_frames


def test_render_turntable_gpu_raises_if_called_directly_without_gpu(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    with pytest.raises(RuntimeError, match="GPU render unavailable"):
        render.render_turntable_gpu(str(tmp_path / "fake.stl"), str(tmp_path / "out"))
