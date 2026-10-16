"""Public, generated STEP fixtures only; no benchmark answers or external CAD."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest
import trimesh

from makerbench import cadquery_backend as cq
from makerbench import step_mesh
from makerbench.render import CompileError

HAS_OCP = importlib.util.find_spec("OCP") is not None
SANDBOX = cq._bubblewrap_available(cq._scrub_environment(os.environ))
if os.environ.get("MAKERBENCH_REQUIRE_STEP") == "1" and not (HAS_OCP and SANDBOX):
    pytest.fail("STEP CI requires both OCP and a working Bubblewrap sandbox", pytrace=False)
if os.environ.get("MAKERBENCH_REQUIRE_STEP") == "1":
    # find_spec alone would miss broken native-library installations. CI must
    # fail at collection in that case, rather than importorskip the real tests.
    import OCP  # noqa: F401
real_step = pytest.mark.skipif(not (HAS_OCP and SANDBOX), reason="optional OCP or bwrap absent")


@pytest.fixture
def make_step(tmp_path):
    pytest.importorskip("OCP")
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    def generate(unit="MM", multi=False, curved=False, scale=1, surface=False):
        # Geometry is defined in mm; the exporter writes real conversion-based
        # inch units and inch coordinates, rather than relabelling a mm file.
        writer = STEPControl_Writer()
        assert Interface_Static.SetCVal_s("write.step.unit", unit)
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        builder.Add(compound, BRepPrimAPI_MakeBox(25.4, 50.8, 76.2).Shape())
        if multi:
            builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(100, 0, 0), 10, 20, 30).Shape())
        if curved:
            axes = gp_Ax2(gp_Pnt(200 * scale, 0, 0), gp_Dir(0, 0, 1))
            builder.Add(compound, BRepPrimAPI_MakeCylinder(axes, 30 * scale, 60 * scale).Shape())
        if surface:
            plane = gp_Pln(gp_Pnt(0, 100, 0), gp_Dir(0, 0, 1))
            builder.Add(compound, BRepBuilderAPI_MakeFace(plane, 0, 10, 0, 10).Face())
        assert writer.Transfer(compound, STEPControl_AsIs) == IFSelect_RetDone
        path = tmp_path / f"{unit}-{multi}-{curved}-{scale}-{surface}.step"
        assert writer.Write(str(path)) == IFSelect_RetDone
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        return path
    return generate


@real_step
def test_loose_surface_is_preserved_alongside_solid(make_step, tmp_path):
    result = step_mesh.convert_step_to_mesh(make_step(surface=True), tmp_path / "out")
    assert len(result.body_paths) == 2
    assert sorted(len(trimesh.load_mesh(p).faces) for p in result.body_paths) == [2, 12]
    assert len(trimesh.load_mesh(result.stl_path).faces) == 14


@real_step
@pytest.mark.parametrize("unit", ["MM", "INCH"])
def test_units_and_grader_ready_mesh(make_step, tmp_path, unit):
    source = make_step(unit=unit)
    if unit == "INCH":
        assert "CONVERSION_BASED_UNIT('INCH'" in source.read_text()
    result = step_mesh.convert_step_to_mesh(source, tmp_path / "out")
    mesh = trimesh.load_mesh(result.stl_path)
    assert mesh.is_watertight
    assert mesh.extents == pytest.approx([25.4, 50.8, 76.2], abs=0.001)
    assert mesh.volume == pytest.approx(25.4 * 50.8 * 76.2, rel=1e-6)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["units"] == "mm"
    assert manifest["source_units"] == ["INCH" if unit == "INCH" else "millimetre"]
    assert result.backend == "step"


@real_step
def test_multiple_bodies_and_byte_identical_fresh_processes(make_step, tmp_path):
    source = make_step(multi=True)
    first = step_mesh.convert_step_to_mesh(source, tmp_path / "one")
    renamed = tmp_path / "renamed.step"
    renamed.write_bytes(source.read_bytes())
    second = step_mesh.convert_step_to_mesh(renamed, tmp_path / "two")
    assert len(first.body_paths) == 2
    assert len(trimesh.load_mesh(first.stl_path).split()) == 2
    assert sorted(round(trimesh.load_mesh(p).volume) for p in first.body_paths) == [6000, 98322]
    for a, b in zip((first.stl_path, first.manifest_path, *first.body_paths),
                    (second.stl_path, second.manifest_path, *second.body_paths)):
        assert a.read_bytes() == b.read_bytes()


@real_step
def test_curved_mesh_is_deterministic_and_bounded(make_step, tmp_path):
    source = make_step(curved=True)
    a = step_mesh.convert_step_to_mesh(source, tmp_path / "a")
    b = step_mesh.convert_step_to_mesh(source, tmp_path / "b")
    assert a.stl_path.read_bytes() == b.stl_path.read_bytes()
    mesh = trimesh.load_mesh(a.stl_path)
    assert mesh.is_watertight
    assert 100 < len(mesh.faces) < 150_000
    manifest = json.loads(a.manifest_path.read_text())
    assert manifest["linear_deflection"] == cq.STL_LINEAR_TOLERANCE_MM
    assert manifest["angular_deflection_rad"] == cq.STL_ANGULAR_TOLERANCE_RAD
    assert manifest["relative"] is True
    # Large features must not acquire the triangle explosion of an absolute
    # 0.01 mm setting: the same cylinder scaled 100x keeps the same density.
    large = step_mesh.convert_step_to_mesh(make_step(curved=True, scale=100), tmp_path / "large")
    assert len(trimesh.load_mesh(large.stl_path).faces) == len(mesh.faces)


@real_step
def test_triangle_and_body_limits_reject_geometry(make_step, tmp_path, monkeypatch):
    from makerbench import step_mesh_worker as worker
    out = tmp_path / "out"
    out.mkdir()
    source = make_step(multi=True)
    monkeypatch.setattr(worker, "MAX_BODIES", 1)
    with pytest.raises(ValueError, match="body limit"):
        worker.convert(str(source), out / "output.stl",
                       cq.STL_LINEAR_TOLERANCE_MM, cq.STL_ANGULAR_TOLERANCE_RAD)
    monkeypatch.setattr(worker, "MAX_BODIES", 1000)
    monkeypatch.setattr(worker, "MAX_TRIANGLES", 10)
    with pytest.raises(ValueError, match="triangle limit"):
        worker.convert(str(source), out / "output.stl",
                       cq.STL_LINEAR_TOLERANCE_MM, cq.STL_ANGULAR_TOLERANCE_RAD)
    assert not (out / "output.stl").exists()


@real_step
def test_malformed_step_cli_has_readable_error_without_traceback(tmp_path):
    source = tmp_path / "bad.step"
    source.write_text("this is not STEP")
    proc = subprocess.run([sys.executable, "-m", "makerbench.step_mesh", str(source),
                           str(tmp_path / "out")], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "malformed or unsupported STEP" in proc.stderr
    assert "Traceback" not in proc.stderr + proc.stdout
    assert not (tmp_path / "out").exists()


def test_size_and_missing_file_errors_before_kernel(tmp_path):
    with pytest.raises(CompileError, match="cannot be read"):
        step_mesh.convert_step_to_mesh(tmp_path / "missing", tmp_path / "out")
    source = tmp_path / "large.step"
    with source.open("wb") as stream:
        stream.truncate(step_mesh.MAX_INPUT_BYTES + 1)
    with pytest.raises(CompileError, match="50 MiB"):
        step_mesh.convert_step_to_mesh(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_timeout_does_not_publish_partial_output(tmp_path, monkeypatch):
    source = tmp_path / "input.step"
    source.write_text("test")
    monkeypatch.setattr(cq, "_bubblewrap_available", lambda env: True)
    monkeypatch.setattr(cq, "_bubblewrap_command", lambda **kwargs: ["synthetic-worker"])
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 120
        assert kwargs["env"].get("TEST_SECRET") is None
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setenv("TEST_SECRET", "synthetic-secret")
    monkeypatch.setattr(step_mesh.subprocess, "run", timeout)
    with pytest.raises(CompileError, match="timed out"):
        step_mesh.convert_step_to_mesh(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_sandbox_fails_closed(tmp_path, monkeypatch):
    source = tmp_path / "input.step"
    source.write_text("test")
    monkeypatch.setattr(cq, "_bubblewrap_available", lambda env: False)
    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        step_mesh.convert_step_to_mesh(source, tmp_path / "out")


def test_missing_kernel_is_environment_failure(tmp_path, monkeypatch):
    source = tmp_path / "input.step"
    source.write_text("test")
    monkeypatch.setattr(cq, "_bubblewrap_available", lambda env: True)
    monkeypatch.setattr(cq, "_bubblewrap_command", lambda **kwargs: ["synthetic-worker"])
    def missing_kernel(command, **kwargs):
        kwargs["stdout"].write(b"STEP_ENVIRONMENT_ERROR: missing kernel\n")
        return subprocess.CompletedProcess(command, 20)
    monkeypatch.setattr(step_mesh.subprocess, "run", missing_kernel)
    with pytest.raises(RuntimeError, match="requirements-step.lock"):
        step_mesh.convert_step_to_mesh(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@real_step
def test_worker_confinement_hides_host_and_network(tmp_path):
    sentinel = tmp_path / "host-only.txt"
    sentinel.write_text("synthetic public control")
    driver = tmp_path / "probe.py"
    driver.write_text(
        "import pathlib, socket\n"
        f"assert not pathlib.Path({str(sentinel)!r}).exists()\n"
        "assert not pathlib.Path('/home/tony').exists()\n"
        "s = socket.socket()\n"
        "s.settimeout(0.1)\n"
        "assert s.connect_ex(('1.1.1.1', 53)) != 0\n"
        "print('confined')\n"
    )
    out = tmp_path / "out"
    out.mkdir()
    proc = subprocess.run(cq._bubblewrap_command(driver_path=driver, script_path=sentinel,
                                                 out_dir=out),
                          env=cq._scrub_environment(os.environ), capture_output=True,
                          text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "confined"
