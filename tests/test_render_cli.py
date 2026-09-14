"""Renderer selection and vote-page provenance tests for Story #695."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from makerbench import cli_arena
from makerbench import render
from makerbench.cli import app as cli_app


runner = CliRunner()


def _fake_turntable(calls: list[dict], actual_renderer: str):
    def render_turntable(mesh_path, out_dir, *, frames, size, renderer):
        calls.append(
            {
                "mesh_path": mesh_path,
                "out_dir": out_dir,
                "frames": frames,
                "size": size,
                "renderer": renderer,
            }
        )
        destination = Path(out_dir)
        destination.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(frames):
            path = destination / f"frame_{index:02d}.png"
            path.write_bytes(b"fake png")
            paths.append(str(path))
        (destination / "turntable_manifest.json").write_text(
            json.dumps(
                {
                    "schema": "makerbench-turntable-render-v1",
                    "renderer": actual_renderer,
                    "frames": [Path(path).name for path in paths],
                }
            ),
            encoding="utf-8",
        )
        return paths

    return render_turntable


def test_vote_commands_expose_renderer_option():
    for command in ("turntable", "vote", "vote-web"):
        result = runner.invoke(
            cli_app,
            ["arena", command, "--help"],
            env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
        )
        assert result.exit_code == 0
        assert "--renderer" in result.stdout
        assert "auto, gpu, or openscad" in result.stdout


def test_turntable_cli_passes_renderer_and_writes_provenance(tmp_path, monkeypatch):
    mesh = tmp_path / "candidate.stl"
    mesh.write_bytes(b"solid fake\nendsolid\n")
    out_dir = tmp_path / "frames"
    calls: list[dict] = []
    monkeypatch.setattr(render, "render_turntable", _fake_turntable(calls, "blender-eevee"))

    result = runner.invoke(
        cli_app,
        [
            "arena",
            "turntable",
            "--mesh",
            str(mesh),
            "--out-dir",
            str(out_dir),
            "--renderer",
            "gpu",
            "--frames",
            "2",
            "--size",
            "256",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert calls[0]["renderer"] == "gpu"
    assert calls[0]["frames"] == 2
    assert calls[0]["size"] == (256, 256)
    assert '"renderer": "blender-eevee"' in result.stdout


def test_openscad_selection_records_blind_vote_page_provenance(tmp_path, monkeypatch):
    mesh = tmp_path / "entrant-name-must-not-leak.stl"
    mesh.write_bytes(b"solid fake\nendsolid\n")
    vote_pages = tmp_path / "vote_pages"
    calls: list[dict] = []
    monkeypatch.setattr(render, "gpu_render_available", lambda: (True, "fake_gpu"))
    monkeypatch.setattr(render, "render_turntable", _fake_turntable(calls, "openscad"))

    frames = cli_arena._stage_turntable_frames(
        mesh,
        "pair-deadbeef",
        "left",
        vote_pages,
        frames=2,
        size=(320, 240),
        renderer="openscad",
    )

    assert calls[0]["renderer"] == "openscad"
    assert frames == (
        "blind/pair-deadbeef-left-f00.png",
        "blind/pair-deadbeef-left-f01.png",
    )
    manifest_path = vote_pages / "vote_page_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["frame_sets"]["pair-deadbeef:left"]
    assert entry["requested_renderer"] == "openscad"
    assert entry["renderer"] == "openscad"
    assert str(mesh) not in manifest_path.read_text(encoding="utf-8")
    assert "entrant-name-must-not-leak" not in manifest_path.read_text(encoding="utf-8")


def test_auto_selects_available_gpu_and_records_actual_renderer(tmp_path, monkeypatch):
    mesh = tmp_path / "candidate.stl"
    mesh.write_bytes(b"solid fake\nendsolid\n")
    calls: list[dict] = []
    monkeypatch.setattr(render, "gpu_render_available", lambda: (True, "fake_gpu"))
    monkeypatch.setattr(
        render,
        "render_turntable",
        _fake_turntable(calls, "blender-eevee"),
    )

    cli_arena._stage_turntable_frames(
        mesh,
        "pair-cafebabe",
        "right",
        tmp_path / "vote_pages",
        frames=1,
        renderer="auto",
    )

    assert calls[0]["renderer"] == "auto"
    manifest = json.loads(
        (tmp_path / "vote_pages" / "vote_page_manifest.json").read_text(encoding="utf-8")
    )
    entry = manifest["frame_sets"]["pair-cafebabe:right"]
    assert entry["requested_renderer"] == "auto"
    assert entry["renderer"] == "blender-eevee"


def test_render_turntable_rejects_unknown_renderer():
    try:
        render.render_turntable("candidate.stl", "frames", renderer="mystery")
    except ValueError as exc:
        assert "auto, gpu, or openscad" in str(exc)
    else:
        raise AssertionError("unknown renderer was accepted")
