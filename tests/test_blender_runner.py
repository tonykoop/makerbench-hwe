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
@pytest.mark.parametrize("scale", ["0.001", "2.0"])
def test_entrant_scene_unit_scale_is_normalised(tmp_path: Path, scale: str) -> None:
    script = tmp_path / "scaled.py"
    script.write_text(
        "import bpy\n"
        f"bpy.context.scene.unit_settings.scale_length = {scale}\n"
        "bpy.context.scene.unit_settings.system = 'IMPERIAL'\n"
        "bpy.ops.mesh.primitive_cube_add(size=1)\n"
        "o = bpy.context.object\n"
        "o.scale = (0.02, 0.03, 0.05)\n"
        "bpy.ops.object.transform_apply(scale=True)\n"
    )
    run = br.run_blender(script, tmp_path / "out")
    assert run.provenance.bbox_mm == (20.0, 30.0, 50.0)  # metres authored, always x1000


@needs_blender
@pytest.mark.blender
def test_relative_out_dir(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "cube.py"
    script.write_text(CUBE_50MM)
    monkeypatch.chdir(tmp_path)
    run = br.run_blender(Path("cube.py"), Path("relative-out"))
    assert run.stl_path == (tmp_path / "relative-out" / "output.stl").resolve()
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


# --- fake-process tests: no Blender needed ---------------------------------

import struct  # noqa: E402
import subprocess  # noqa: E402


def _write_stl(path: Path) -> None:
    tri = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 10, 0, 0, 0, 20, 0, 0)
    path.write_bytes(br.STL_HEADER.ljust(80, b"\0") + struct.pack("<I", 1) + tri)


class _Fake:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch,
        *,
        version="4.2.23",
        rc=0,
        ok=True,
        write=True,
        timeout=False,
    ):
        self.calls: list[dict] = []
        self.binary = tmp_path / "fake-blender"
        self.binary.write_bytes(b"fake")

        def fake_run(cmd, **kw):
            self.calls.append({"cmd": [str(c) for c in cmd], **kw})
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, f"Blender {version} LTS\n", "")
            if timeout:
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            if write:
                _write_stl(Path(cmd[-1]))
            out = (
                "BLENDER_DRIVER_OK\n"
                if ok
                else "BLENDER_DRIVER_ERROR: entrant script raised: boom\n"
            )
            return subprocess.CompletedProcess(cmd, rc, out, "")

        monkeypatch.setattr(br.subprocess, "run", fake_run)


@pytest.fixture
def entrant(tmp_path: Path) -> Path:
    p = tmp_path / "e.py"
    p.write_text("import bpy\n")
    return p


def test_fake_success_orchestration_and_provenance(tmp_path: Path, monkeypatch, entrant) -> None:
    fake = _Fake(tmp_path, monkeypatch)
    run = br.run_blender(entrant, tmp_path / "out", blender_bin=fake.binary, timeout_s=7)
    launch = fake.calls[-1]
    for flag in ("-b", "--factory-startup", "--offline-mode", "--threads"):
        assert flag in launch["cmd"]
    assert launch["timeout"] == 7
    assert Path(launch["cwd"]) != tmp_path  # fresh temp cwd
    assert Path(launch["cmd"][-1]).is_absolute() and Path(launch["cmd"][-2]).is_absolute()
    prov = run.provenance
    assert prov.is_pinned and prov.triangles == 1 and prov.bbox_mm == (10.0, 20.0, 0.0)
    assert prov.blender_binary_sha256 == hashlib.sha256(b"fake").hexdigest()
    assert prov.stl_sha256 == hashlib.sha256(run.stl_path.read_bytes()).hexdigest()
    assert json.loads(run.provenance_path.read_text())["stl_sha256"] == prov.stl_sha256


def test_fake_relative_paths_are_absolutised(tmp_path: Path, monkeypatch, entrant) -> None:
    fake = _Fake(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    br.run_blender(Path("e.py"), Path("rel-out"), blender_bin=fake.binary)
    assert Path(fake.calls[-1]["cmd"][-1]) == (tmp_path / "rel-out" / "output.stl").resolve()


def test_fake_proxy_vars_removed(tmp_path: Path, monkeypatch, entrant) -> None:
    for k in ("http_proxy", "HTTPS_PROXY", "all_proxy", "NO_PROXY"):
        monkeypatch.setenv(k, "http://proxy.invalid:1")
    fake = _Fake(tmp_path, monkeypatch)
    br.run_blender(entrant, tmp_path / "out", blender_bin=fake.binary)
    env = fake.calls[-1]["env"]
    assert not any(k.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"} for k in env)
    assert env["PYTHONHASHSEED"] == "0"


def test_fake_wrong_version_rejected(tmp_path: Path, monkeypatch, entrant) -> None:
    fake = _Fake(tmp_path, monkeypatch, version="4.1.0")
    with pytest.raises(br.BlenderRunnerError, match="not the pinned"):
        br.run_blender(entrant, tmp_path / "out", blender_bin=fake.binary)


def test_fake_subprocess_failure(tmp_path: Path, monkeypatch, entrant) -> None:
    fake = _Fake(tmp_path, monkeypatch, rc=3, ok=False, write=False)
    with pytest.raises(br.BlenderRunnerError, match="boom"):
        br.run_blender(entrant, tmp_path / "out", blender_bin=fake.binary)


def test_fake_missing_output_is_failure(tmp_path: Path, monkeypatch, entrant) -> None:
    fake = _Fake(tmp_path, monkeypatch, write=False)
    with pytest.raises(br.BlenderRunnerError):
        br.run_blender(entrant, tmp_path / "out", blender_bin=fake.binary)


def test_fake_timeout(tmp_path: Path, monkeypatch, entrant) -> None:
    fake = _Fake(tmp_path, monkeypatch, timeout=True)
    with pytest.raises(br.BlenderRunnerError, match="timed out after 5s"):
        br.run_blender(entrant, tmp_path / "out", blender_bin=fake.binary, timeout_s=5)
