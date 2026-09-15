"""Tests for sandboxed multi-view and section-plane renders (#795).

Real-sandbox tests run OpenSCAD inside Bubblewrap. They skip when the sandbox
is missing, unless ``MAKERBENCH_REQUIRE_SANDBOX=1`` (CI), where a missing
sandbox is a failure instead of a silent skip.
"""

from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

import pytest
import trimesh

from makerbench import cadquery_backend, measure, render, render_view, scad_sandbox

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()

if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail(
        "MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD render sandbox cannot start",
        pytrace=False,
    )

needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

#: Pixel-counted cut area vs. the mesh section, at the default 800x600 image.
CUT_AREA_TOLERANCE_REL = 0.03
SMALL = (320, 240)


def _stl(tmp_path: Path, mesh: trimesh.Trimesh, name: str = "part.stl") -> Path:
    path = tmp_path / name
    mesh.export(path)
    return path


def test_view_set_is_fixed_and_unique():
    names = [view.name for view in render_view.VIEW_SET]
    assert names == ["iso", "front", "back", "left", "right", "top", "bottom"]
    assert len({view.rotation_deg for view in render_view.VIEW_SET}) == len(names)


def test_axis_section_rejects_unknown_axis():
    with pytest.raises(ValueError, match="axis must be"):
        render_view.axis_section("w", 1.0)


def test_unavailable_sandbox_fails_closed_and_starts_no_process(tmp_path, monkeypatch):
    stl = _stl(tmp_path, trimesh.creation.box(extents=[5, 5, 5]))
    started: list[object] = []

    def refuse(*args, **kwargs):
        started.append(args)
        raise AssertionError("no host process may start when the sandbox is unavailable")

    monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    with pytest.raises(render_view.SandboxUnavailable):
        render_view.render_mesh_views(stl, tmp_path / "out")
    assert started == []
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_zero_byte_image_is_a_failure(tmp_path, monkeypatch):
    stl = _stl(tmp_path, trimesh.creation.box(extents=[5, 5, 5]))
    monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
    monkeypatch.setattr(scad_sandbox, "build_command", lambda **kwargs: ["fake"])

    def fake_run(cmd, *, timeout, stage):
        (tmp_path / "out" / stage).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scad_sandbox, "_run_sandboxed", fake_run)

    result = render_view.render_mesh_views(stl, tmp_path / "out", views=render_view.VIEW_SET[:1])

    assert result.ok is False
    assert "0-byte image" in result.error
    assert result.images == ()


@pytest.mark.parametrize("name", ["../escape", "a/b", "..", ".hidden", "", "x" * 65, "sp ace"])
@pytest.mark.parametrize("kind", ["view", "section"])
def test_unsafe_names_are_refused_before_any_file_is_touched(tmp_path, monkeypatch, name, kind):
    # Pre-create the traversal target and an intermediate dir so an unchecked
    # exists()/unlink() on "<out>/view-../escape.png" would really reach it.
    out_dir = tmp_path / "out"
    (out_dir / "view-..").mkdir(parents=True)
    (out_dir / "section-..").mkdir(parents=True)
    sentinel = out_dir / "escape.png"
    sentinel.write_bytes(b"outside the image namespace")
    outside = tmp_path / "escape.png"
    outside.write_bytes(b"host file")
    stl = _stl(tmp_path, trimesh.creation.box(extents=[5, 5, 5]))
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
    monkeypatch.setattr(scad_sandbox, "build_command",
                        lambda **kw: pytest.fail("no render may start for an unsafe name"))
    views = [render_view.ViewSpec(name, (0.0, 0.0, 0.0))] if kind == "view" else []
    sections = [render_view.SectionSpec(name, (0, 0, 0), (0, 0, 1))] if kind == "section" else []

    result = render_view.render_mesh_views(stl, out_dir, views=views, sections=sections)

    assert result.ok is False and "unsafe view/section name" in result.error
    assert sentinel.read_bytes() == b"outside the image namespace"
    assert outside.read_bytes() == b"host file"
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before


def test_render_one_refuses_an_output_path_outside_out_dir(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = tmp_path / "victim.png"
    victim.write_bytes(b"keep")
    with pytest.raises(ValueError, match="outside the output directory"):
        render_view._render_one(tmp_path, out_dir, "view.scad", "../victim.png", [], SMALL)
    assert victim.read_bytes() == b"keep"


def test_empty_mesh_is_an_error_not_a_render(tmp_path, monkeypatch):
    monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
    empty = tmp_path / "empty.stl"
    empty.write_text("solid empty\nendsolid empty\n", encoding="utf-8")
    result = render_view.render_mesh_views(empty, tmp_path / "out")
    assert result.ok is False and "empty mesh" in result.error


def test_zero_normal_section_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: True)
    stl = _stl(tmp_path, trimesh.creation.box(extents=[5, 5, 5]))
    bad = render_view.SectionSpec("bad", (0, 0, 0), (0, 0, 0))
    result = render_view.render_mesh_views(stl, tmp_path / "out", views=(), sections=[bad])
    assert result.ok is False and "non-zero" in result.error


@needs_sandbox
def test_views_are_rendered_in_the_sandbox_and_deterministic(tmp_path):
    # Asymmetric (an L), so front and back silhouettes genuinely differ.
    base = trimesh.creation.box(extents=[10, 20, 5])
    post = trimesh.creation.box(extents=[3, 3, 12])
    post.apply_translation([3.5, 0.0, 8.5])
    stl = _stl(tmp_path, trimesh.util.concatenate([base, post]))
    views = render_view.VIEW_SET[:3]

    first = render_view.render_mesh_views(stl, tmp_path / "a", views=views, image_size=SMALL)
    second = render_view.render_mesh_views(stl, tmp_path / "b", views=views, image_size=SMALL)

    assert first.ok, first.error
    assert [image.file for image in first.images] == [
        "view-iso.png", "view-front.png", "view-back.png"
    ]
    assert all(image.bytes > 0 for image in first.images)
    assert [i.sha256 for i in first.images] == [i.sha256 for i in second.images]
    # Different cameras really are different pictures.
    assert len({image.sha256 for image in first.images}) == 3


@needs_sandbox
def test_every_image_is_rendered_through_the_bwrap_command(tmp_path, monkeypatch):
    stl = _stl(tmp_path, trimesh.creation.box(extents=[6, 6, 6]))
    built: list[list[str]] = []
    real_build = scad_sandbox.build_command

    def spy(**kwargs):
        cmd = real_build(**kwargs)
        built.append(cmd)
        return cmd

    monkeypatch.setattr(scad_sandbox, "build_command", spy)
    result = render_view.render_mesh_views(
        stl, tmp_path / "out", views=render_view.VIEW_SET[:2],
        sections=[render_view.axis_section("z", 0.0)], image_size=SMALL,
    )

    assert result.ok, result.error
    assert len(built) == len(result.images) == 3
    for cmd in built:
        assert Path(cmd[0]).name == "bwrap"
        assert "--unshare-net" in cmd and "--clearenv" in cmd


@needs_sandbox
def test_render_sandbox_cannot_read_host_files(tmp_path):
    secret = tmp_path / "host-secret.stl"
    trimesh.creation.box(extents=[50, 50, 50]).export(secret)
    stl = _stl(tmp_path, trimesh.creation.box(extents=[5, 5, 5]))
    # A mesh path pointing outside the work dir must not resolve inside bwrap.
    work = tmp_path / "work"
    work.mkdir()
    (work / "probe.scad").write_text(f'import("{secret.as_posix()}");\n', encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    cmd = scad_sandbox.build_command(
        work_dir=work, out_dir=out,
        openscad_args=["-o", f"{scad_sandbox.OUT_DIR}/probe.stl",
                       f"{scad_sandbox.WORK_DIR}/probe.scad"],
        with_xvfb=False,
    )
    proc = scad_sandbox._run_sandboxed(cmd, timeout=60, stage="probe")
    assert "Can't open import file" in proc.stderr
    assert render_view.render_mesh_views(stl, tmp_path / "views",
                                         views=render_view.VIEW_SET[:1], image_size=SMALL).ok


@needs_sandbox
@pytest.mark.parametrize(
    "mesh_factory, section",
    [
        (lambda: trimesh.creation.icosphere(subdivisions=5, radius=10.0),
         render_view.axis_section("z", 0.0, "sphere-equator")),
        (lambda: trimesh.creation.icosphere(subdivisions=5, radius=10.0),
         render_view.axis_section("x", 6.0, "sphere-x6")),
        (lambda: trimesh.creation.annulus(r_min=6.0, r_max=10.0, height=40.0, sections=256),
         render_view.axis_section("z", 3.0, "tube-bore")),
        (lambda: trimesh.creation.box(extents=[30.0, 12.0, 8.0]),
         render_view.SectionSpec("box-oblique", (0.0, 0.0, 0.0), (0.0, 1.0, 1.0))),
    ],
    ids=["sphere-equator", "sphere-offset", "tube-bore", "box-oblique"],
)
def test_section_image_cut_area_matches_the_measure_helper(tmp_path, mesh_factory, section):
    mesh = mesh_factory()
    mesh.apply_translation([4.0, -3.0, 2.0])
    moved = render_view.SectionSpec(
        section.name,
        tuple(o + d for o, d in zip(section.origin, (4.0, -3.0, 2.0))),
        section.normal,
    )
    stl = _stl(tmp_path, mesh)

    result = render_view.render_mesh_views(stl, tmp_path / "out", views=(), sections=[moved])

    assert result.ok, result.error
    (image,) = result.images
    assert image.kind == "section" and image.file == f"section-{section.name}.png"
    expected = measure.section_area(mesh, origin=moved.origin, normal=moved.normal)
    assert expected.ok and expected.value > 0
    assert abs(image.cut_area_mm2 - expected.value) / expected.value <= CUT_AREA_TOLERANCE_REL


@needs_sandbox
def test_openscad_candidate_compiles_through_the_scad_sandbox(tmp_path, monkeypatch):
    source = tmp_path / "candidate.scad"
    source.write_text("difference() { cube([20, 20, 10], center=true); "
                      "cylinder(r=5, h=20, center=true, $fn=96); }\n", encoding="utf-8")
    calls: list[Path] = []
    real = scad_sandbox.compile_scad_sandboxed

    def spy(scad_path, out_dir):
        calls.append(Path(scad_path))
        return real(scad_path, out_dir)

    monkeypatch.setattr(scad_sandbox, "compile_scad_sandboxed", spy)
    section = render_view.axis_section("z", 0.0, "mid")

    result = render_view.render_view(source, tmp_path / "out", backend="openscad",
                                     views=render_view.VIEW_SET[:1], sections=[section])

    assert result.ok, result.error
    assert calls == [source]
    assert result.stl_file == "compile/output.stl"
    expected = 400.0 - math.pi * 25.0
    cut = result.images[-1].cut_area_mm2
    assert abs(cut - expected) / expected <= CUT_AREA_TOLERANCE_REL


@needs_sandbox
def test_openscad_compile_error_is_a_failed_result(tmp_path):
    source = tmp_path / "broken.scad"
    source.write_text("cube([1, 1, 1]\n", encoding="utf-8")
    result = render_view.render_view(source, tmp_path / "out", backend="openscad", views=())
    assert result.ok is False and result.error.startswith("compile failed:")


def test_cadquery_backend_is_the_bubblewrap_compiler(tmp_path, monkeypatch):
    calls: list[Path] = []

    def fake_compile(script_path, out_dir):
        calls.append(Path(script_path))
        raise render.CompileError("candidate raised")

    monkeypatch.setattr(cadquery_backend, "compile_cadquery_to_artifacts", fake_compile)
    result = render_view.render_view(tmp_path / "c.py", tmp_path / "out", backend="cadquery")
    assert calls == [tmp_path / "c.py"]
    assert result.ok is False and "candidate raised" in result.error


@needs_sandbox
@pytest.mark.skipif(not cadquery_backend.cadquery_available(), reason="CadQuery extra not installed")
def test_cadquery_candidate_renders_through_bubblewrap(tmp_path):
    source = tmp_path / "candidate.py"
    source.write_text(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').circle(10).circle(6).extrude(20)\n",
        encoding="utf-8",
    )
    section = render_view.axis_section("z", 10.0, "ring")
    result = render_view.render_view(source, tmp_path / "out", backend="cadquery",
                                     views=render_view.VIEW_SET[:1], sections=[section])
    assert result.ok, result.error
    expected = math.pi * (100.0 - 36.0)
    assert abs(result.images[-1].cut_area_mm2 - expected) / expected <= CUT_AREA_TOLERANCE_REL


def test_unknown_backend_is_refused(tmp_path):
    with pytest.raises(ValueError, match="backend must be"):
        render_view.render_view(tmp_path / "x", tmp_path / "out", backend="blender")
