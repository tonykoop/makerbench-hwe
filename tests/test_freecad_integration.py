"""Opt-in tests for the checksum-pinned headless AppImage (separate CI job)."""

import math
import os
import re
import struct
import time
from pathlib import Path

import pytest

from makerbench import freecad_backend as backend

pytestmark = pytest.mark.skipif(
    os.environ.get("MAKERBENCH_FREECAD_INTEGRATION") != "1",
    reason="Opt in with MAKERBENCH_FREECAD_INTEGRATION=1 and install the pinned AppImage",
)


def stable_step(data: bytes) -> bytes:
    """Blank only HEADER FILE_NAME's first two quoted values (name, timestamp)."""
    header, separator, rest = data.partition(b"ENDSEC;")
    assert separator and header.startswith(b"ISO-10303-21;\nHEADER;")
    # STEP strings escape a quote as two quotes. Preserve all other bytes,
    # including remaining FILE_NAME fields, newlines, schema, and the DATA section.
    header, count = re.subn(
        rb"(?m)^FILE_NAME\('(?:[^']|'')*','(?:[^']|'')*'",
        b"FILE_NAME('',''",
        header,
    )
    assert count == 1
    return header + separator + rest


@pytest.fixture
def runtime():
    value = os.environ.get("MAKERBENCH_FREECAD_APPDIR")
    assert value, "Integration job requires MAKERBENCH_FREECAD_APPDIR (must not skip)"
    return Path(value)


@pytest.mark.parametrize(
    "shape,volume",
    [
        ("Part.makeBox(10, 20, 30)", 6000),
        ("Part.makeCylinder(5, 12)", math.pi * 25 * 12),
    ],
)
def test_headless_exports_are_deterministic(tmp_path, runtime, shape, volume, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    script = tmp_path / "part.py"
    script.write_text(
        "import os, sys, Part, FreeCAD\n"
        "assert 'FreeCADGui' not in sys.modules\n"
        "assert not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY')\n"
        "assert not os.environ.get('GH_TOKEN')\n"
        "assert len(FreeCAD.listDocuments()) == 1 and not doc.Objects\n"
        f"result = {shape}\n"
    )
    monkeypatch.setenv("GH_TOKEN", "synthetic-freecad-env-control")
    first = backend.run_freecad(script, tmp_path / "one", appdir=runtime)
    time.sleep(1.1)  # Ensure header timestamps differ; distinct scratch/output dirs too.
    second = backend.run_freecad(script, tmp_path / "two", appdir=runtime)
    assert first.stl_path.read_bytes() == second.stl_path.read_bytes()
    stl = first.stl_path.read_bytes()
    facets = struct.unpack("<I", stl[80:84])[0]
    assert facets > 0 and len(stl) == 84 + facets * 50  # binary STL, not empty/text
    # Independent mesh-volume check: identical bytes alone could be identically
    # wrong. Binary STL facets are normal, three vertices, then attribute bytes.
    six_volume = 0.0
    for record in struct.iter_unpack("<12fH", stl[84:]):
        ax, ay, az, bx, by, bz, cx, cy, cz = record[3:12]
        six_volume += ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)
    assert abs(six_volume / 6) == pytest.approx(volume, rel=0.002)
    a, b = first.step_path.read_bytes(), second.step_path.read_bytes()
    assert a != b  # Timestamp control proves normalization is exercised.
    assert stable_step(a) == stable_step(b)
    assert b"MANIFOLD_SOLID_BREP" in a
    assert b"SI_UNIT(.MILLI.,.METRE.)" in a
    assert b"AUTOMOTIVE_DESIGN" in a
    # Reopen the retained STEP with the real kernel, rather than accepting a
    # plausible-looking text file. No oracle or scored artifact is involved.
    reader = tmp_path / "read_step.py"
    reader.write_text(
        f"import Part\nresult = Part.read({str(first.step_path)!r})\n"
        f"assert abs(result.Volume - {volume!r}) < 1e-6\n"
    )
    backend.run_freecad(reader, tmp_path / "roundtrip", appdir=runtime)
    assert first.provenance == second.provenance
    assert first.provenance["freecad_version"] == "1.1.4"
    assert first.provenance["occt_version"] == "7.8.1"
    assert first.provenance["distribution"]["sha256"] == backend.APPIMAGE_SHA256
    assert first.provenance["export"] == {
        "linear_deflection_mm": backend.LINEAR_DEFLECTION_MM,
        "angular_deflection_rad": backend.ANGULAR_DEFLECTION_RAD,
        "relative": False,
        "stl_format": "binary",
        "step_schema": "AP214IS",
        "step_unit": "MM",
    }


def test_step_normalization_keeps_other_header_and_data_bytes():
    base = (
        b"ISO-10303-21;\nHEADER;\nFILE_NAME('a','time',('author'),'processor');\n"
        b"FILE_SCHEMA(('schema'));\nENDSEC;\nDATA;\n#1 = VALUE('a');\nENDSEC;\n"
    )
    assert stable_step(base) == stable_step(base.replace(b"'a','time'", b"'b','later'"))
    for before, after in [
        (b"author", b"other"),
        (b"processor", b"other"),
        (b"schema", b"other"),
        (b"VALUE('a')", b"VALUE('b')"),
    ]:
        assert stable_step(base) != stable_step(base.replace(before, after))


def test_document_object_export(tmp_path, runtime):
    script = tmp_path / "part.py"
    script.write_text(
        "import Part\nobj = doc.addObject('Part::Feature', 'Box')\n"
        "obj.Shape = Part.makeBox(2, 3, 4)\n"
    )
    artifacts = backend.run_freecad(script, tmp_path / "out", appdir=runtime)
    assert artifacts.stl_path.stat().st_size > 84


@pytest.mark.parametrize(
    "source",
    [
        "result = 42",
        "raise RuntimeError('synthetic failure')",
        "import Part\nresult = Part.Shape()",
        "pass",
    ],
)
def test_failures_do_not_publish_artifacts(tmp_path, runtime, source):
    script = tmp_path / "part.py"
    script.write_text(source)
    with pytest.raises(backend.FreeCADError, match="FreeCAD failed"):
        backend.run_freecad(script, tmp_path / "out", appdir=runtime)
    assert not (tmp_path / "out").exists()


def test_timeout_and_stale_output(tmp_path, runtime):
    script = tmp_path / "part.py"
    script.write_text("import time\ntime.sleep(30)\n")
    with pytest.raises(backend.FreeCADError, match="timed out"):
        backend.run_freecad(script, tmp_path / "out", appdir=runtime, timeout_s=0.5)
    assert not (tmp_path / "out").exists()
    out = tmp_path / "stale"
    out.mkdir()
    (out / "output.stl").write_bytes(b"old")
    with pytest.raises(ValueError, match="must be empty"):
        backend.run_freecad(script, out, appdir=runtime)
    assert (out / "output.stl").read_bytes() == b"old"
