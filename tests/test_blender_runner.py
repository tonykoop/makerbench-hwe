"""Tests for the deterministic Blender runner (#1040).

Unit tests need no Blender. The ``blender`` tests run only when the pinned
binary is present (``python scripts/fetch_blender.py``); CI runs them in the
separate cached ``blender-runner`` job, never in the unit-test matrix.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from makerbench import blender_runner as br

BLENDER = br.resolve_blender()
needs_blender = pytest.mark.skipif(
    BLENDER is None
    or not BLENDER.is_file()
    or br.parse_version(
        __import__("subprocess")
        .run([str(BLENDER), "-b", "--version"], capture_output=True, text=True)
        .stdout
    )
    != br.PINNED_VERSION,
    reason="pinned Blender not installed (scripts/fetch_blender.py)",
)

CUBE_50MM = """
import bpy
bpy.ops.mesh.primitive_cube_add(size=0.05)  # 0.05 m = 50 mm
"""


def test_unit_scale_to_mm() -> None:
    assert br.unit_scale_to_mm() == 1000.0
    assert br.unit_scale_to_mm(0.001) == 1.0


def test_pin_is_well_formed() -> None:
    assert len(br.PINNED_SHA256) == 64 and int(br.PINNED_SHA256, 16) >= 0
    assert br.PINNED_VERSION in br.PINNED_TARBALL
    assert not str(br.CACHE_DIR).startswith(str(Path(br.__file__).resolve().parents[1]))


def test_parse_version() -> None:
    assert br.parse_version("Blender 4.2.23 LTS\n\tbuild date") == "4.2.23"
    with pytest.raises(br.BlenderRunnerError):
        br.parse_version("garbage")


def test_missing_binary_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BLENDER_BIN", "definitely-not-a-blender")
    script = tmp_path / "e.py"
    script.write_text(CUBE_50MM)
    with pytest.raises(FileNotFoundError):
        br.run_blender(script, tmp_path / "out")


def test_stl_stats_rejects_malformed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.stl"
    bad.write_bytes(b"\0" * 84 + b"x")
    with pytest.raises(br.BlenderRunnerError):
        br.stl_stats(bad)


@needs_blender
@pytest.mark.blender
def test_known_size_cube_is_normalised_to_mm(tmp_path: Path) -> None:
    script = tmp_path / "cube.py"
    script.write_text(CUBE_50MM)
    run = br.run_blender(script, tmp_path / "out")
    assert run.provenance.bbox_mm == (50.0, 50.0, 50.0)
    assert run.provenance.triangles == 12
    assert run.provenance.is_pinned and run.provenance.blender_version == br.PINNED_VERSION


@needs_blender
@pytest.mark.blender
def test_scene_unit_scale_is_honoured(tmp_path: Path) -> None:
    script = tmp_path / "mmscene.py"
    script.write_text(
        "import bpy\n"
        "bpy.context.scene.unit_settings.scale_length = 0.001\n"
        "bpy.ops.mesh.primitive_cube_add(size=50)\n"  # 50 BU * 0.001 m = 50 mm
    )
    run = br.run_blender(script, tmp_path / "out")
    assert run.provenance.bbox_mm == (50.0, 50.0, 50.0)


@needs_blender
@pytest.mark.blender
def test_modifiers_and_triangulation_are_applied(tmp_path: Path) -> None:
    script = tmp_path / "mod.py"
    script.write_text(
        CUBE_50MM
        + "o = bpy.context.object\n"
        + "m = o.modifiers.new('arr', 'ARRAY'); m.count = 2; m.relative_offset_displace = (1.0, 0, 0)\n"
    )
    run = br.run_blender(script, tmp_path / "out")
    assert run.provenance.triangles == 24
    assert run.provenance.bbox_mm == (100.0, 50.0, 50.0)  # relative offset 1.0: cubes abut


@needs_blender
@pytest.mark.blender
def test_export_is_byte_identical_across_runs(tmp_path: Path) -> None:
    script = tmp_path / "det.py"
    script.write_text(
        CUBE_50MM
        + "bpy.ops.mesh.primitive_uv_sphere_add(radius=0.02, location=(0.1, 0, 0))\n"
        + "bpy.context.object.modifiers.new('sub', 'SUBSURF')\n"
    )
    a = br.run_blender(script, tmp_path / "a")
    b = br.run_blender(script, tmp_path / "b")
    assert a.stl_path.read_bytes() == b.stl_path.read_bytes()
    assert a.provenance.stl_sha256 == b.provenance.stl_sha256
    assert hashlib.sha256(a.stl_path.read_bytes()).hexdigest() == a.provenance.stl_sha256
    assert json.loads(a.provenance_path.read_text())["blender_version"] == br.PINNED_VERSION


@needs_blender
@pytest.mark.blender
def test_entrant_error_and_empty_scene_raise(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("raise ValueError('boom')\n")
    with pytest.raises(br.BlenderRunnerError, match="boom"):
        br.run_blender(bad, tmp_path / "o1")
    empty = tmp_path / "empty.py"
    empty.write_text("import bpy\n")
    with pytest.raises(br.BlenderRunnerError, match="no MESH"):
        br.run_blender(empty, tmp_path / "o2")
