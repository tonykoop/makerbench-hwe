"""Unit tests for headless GPU render driver & fallback (Story #695)."""

from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from makerbench import render


def _fake_blender(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "blender"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys

script_path = pathlib.Path(sys.argv[sys.argv.index('--python') + 1])
config_path = pathlib.Path(sys.argv[sys.argv.index('--config') + 1])
config = json.loads(config_path.read_text(encoding='utf-8'))
out_dir = pathlib.Path(config['out_dir'])
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / 'fake_blender_capture.json').write_text(json.dumps({
    'config': config,
    'script': script_path.read_text(encoding='utf-8'),
}), encoding='utf-8')
for index in range(config['frames']):
    (out_dir / f'frame_{index:02d}.png').write_bytes(b'fake png')
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


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
    manifest = json.loads(
        (tmp_path / "frames" / "turntable_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["renderer"] == "openscad"


def test_render_turntable_gpu_raises_if_called_directly_without_gpu(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    with pytest.raises(RuntimeError, match="GPU render unavailable"):
        render.render_turntable_gpu(str(tmp_path / "fake.stl"), str(tmp_path / "out"))


def test_gpu_driver_uses_static_script_bbox_framing_and_json_sidecar(tmp_path, monkeypatch):
    fake_blender = _fake_blender(tmp_path)
    monkeypatch.setenv("PATH", f"{fake_blender.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(render, "BLENDER_BIN", "blender")
    monkeypatch.setattr(render, "gpu_render_available", lambda: (True, "fake_gpu"))
    mesh_path = tmp_path / "candidate with spaces.stl"
    mesh_path.write_text("solid fake\nendsolid\n", encoding="utf-8")
    out_dir = tmp_path / "frames with spaces"

    frames = render.render_turntable_gpu(
        str(mesh_path), str(out_dir), frames=3, size=(640, 480), elevation=35.0
    )

    assert len(frames) == 3
    capture = json.loads((out_dir / "fake_blender_capture.json").read_text(encoding="utf-8"))
    assert capture["config"] == {
        "mesh_path": str(mesh_path.resolve()),
        "out_dir": str(out_dir.resolve()),
        "frames": 3,
        "size": [640, 480],
        "elevation": 35.0,
    }
    assert str(mesh_path.resolve()) not in capture["script"]
    assert "bound_box" in capture["script"]
    assert all(name in capture["script"] for name in ('"Key"', '"Fill"', '"Rim"'))
    assert "BLENDER_EEVEE" in capture["script"]
    assert "use_gtao" in capture["script"]
    assert "use_freestyle" in capture["script"]
    assert "linestyles.new" in capture["script"]
    manifest = json.loads((out_dir / "turntable_manifest.json").read_text(encoding="utf-8"))
    assert manifest["renderer"] == "blender-eevee"
    assert manifest["frames"] == ["frame_00.png", "frame_01.png", "frame_02.png"]
