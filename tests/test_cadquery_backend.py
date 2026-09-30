"""Tests for the sandboxed CadQuery arena compiler (#752)."""

from __future__ import annotations

import subprocess

import pytest

from makerbench import cadquery_backend, render


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_secret_name_scrub_is_case_insensitive_and_preserves_benign_values():
    scrubbed = cadquery_backend._scrub_environment(
        {
            "PATH": "/bin",
            "GH_TOKEN": "gh-secret",
            "GITHUB_ACTIONS": "true",
            "apiKey": "api-secret",
            "CLIENT_SECRET_VALUE": "client-secret",
            "TOKENIZER_MODE": "also-scrubbed-by-contract",
            "BENIGN": "kept",
        }
    )

    assert scrubbed == {"PATH": "/bin", "BENIGN": "kept"}


def test_missing_bubblewrap_fails_closed_before_entrant_execution(tmp_path, monkeypatch):
    script = tmp_path / "entrant.py"
    script.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    monkeypatch.setattr(cadquery_backend, "_bubblewrap_available", lambda _env: False)

    with pytest.raises(RuntimeError, match="filesystem sandbox unavailable"):
        cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")


def test_missing_cadquery_is_an_environment_error(tmp_path, monkeypatch):
    script = tmp_path / "entrant.py"
    script.write_text("result = None\n", encoding="utf-8")
    monkeypatch.setattr(cadquery_backend, "_bubblewrap_available", lambda _env: True)
    monkeypatch.setattr(cadquery_backend.shutil, "which", lambda _name: "/usr/bin/bwrap")

    def fake_run(cmd, **kwargs):
        del cmd, kwargs
        return _completed(
            returncode=21,
            stdout="CADQUERY_DRIVER_ENVIRONMENT_ERROR: cadquery import failed: ModuleNotFoundError\n",
        )

    monkeypatch.setattr(cadquery_backend.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="environment failure"):
        cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")


pytestmark_real = pytest.mark.skipif(
    not cadquery_backend.cadquery_available(), reason="cadquery optional dependency is absent"
)

pytestmark_build123d = pytest.mark.skipif(
    not cadquery_backend.build123d_available(),
    reason="build123d optional dependency is absent",
)


@pytestmark_real
class TestRealCadQueryCompiler:
    def test_good_workplane_retains_step_and_derives_mesh_and_preview(self, tmp_path):
        script = tmp_path / "good.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(60, 40, 20)\n",
            encoding="utf-8",
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

        assert artifacts.stl_path.is_file() and artifacts.stl_path.stat().st_size > 0
        assert artifacts.png_path.is_file() and artifacts.png_path.stat().st_size > 0
        step = artifacts.stl_path.with_name("output.step")
        assert step.is_file() and step.read_text(encoding="utf-8", errors="ignore").startswith(
            "ISO-10303-21"
        )
        volume_warning = next(
            warning
            for warning in artifacts.warnings
            if warning.startswith("brep_mesh_volume:")
        )
        assert "brep_mm3=48000.000" in volume_warning
        assert "mesh_mm3=48000.000" in volume_warning
        assert "relative_delta=0.000000" in volume_warning

    def test_brep_volume_is_the_in_memory_shape_not_the_step_readback(self, tmp_path):
        # #902: an ellipsoid built with transformGeometry (BSpline faces) and unioned with a
        # box re-reads from the retained STEP with a wildly wrong volume in OCP (3.5k mm3
        # against ~192k), while the in-memory B-rep and the mesh agree. The advisory must
        # compare the mesh against the in-memory volume.
        script = tmp_path / "ellipsoid.py"
        script.write_text(
            """import cadquery as cq
s = cq.Solid.makeSphere(1.0, angleDegrees1=-90, angleDegrees2=90)
e = s.transformGeometry(cq.Matrix([[54, 0, 0, 0], [0, 33, 0, 0], [0, 0, 25.3, 0]]))
box = cq.Workplane('XY').box(24, 17, 12, centered=False).translate((46, -8.5, -6))
result = cq.Workplane('XY').add(e).union(box)
""",
            encoding="utf-8",
        )
        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")
        fields = {
            w.split(":")[0]: dict(kv.strip().split("=") for kv in w.split(":", 1)[1].split(";"))
            for w in artifacts.warnings
            if w.startswith(("brep_mesh_volume:", "step_roundtrip_volume:"))
        }
        assert 185_000 < float(fields["brep_mesh_volume"]["brep_mm3"]) < 195_000
        assert float(fields["brep_mesh_volume"]["relative_delta"]) < 0.02
        # The STEP read-back is reported separately; its value is OCP's business, so only
        # the presence and the shared in-memory reference are asserted.
        assert fields["step_roundtrip_volume"]["brep_mm3"] == fields["brep_mesh_volume"]["brep_mm3"]

    def test_step_roundtrip_matches_for_a_plain_solid(self, tmp_path):
        script = tmp_path / "box.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(60, 40, 20)\n", encoding="utf-8"
        )
        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")
        rt = next(w for w in artifacts.warnings if w.startswith("step_roundtrip_volume:"))
        assert "brep_mm3=48000.000" in rt and "step_mm3=48000.000" in rt
        assert "relative_delta=0.000000" in rt

    def test_optional_fixture_input_is_readable_but_not_writable(self, tmp_path):
        seed_script = tmp_path / "seed.py"
        seed_script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 10, 5)\n",
            encoding="utf-8",
        )
        seed_artifacts = cadquery_backend.compile_cadquery_to_artifacts(
            seed_script, tmp_path / "seed-out"
        )
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        seed_step = seed_artifacts.stl_path.with_name("output.step")
        (inputs / "input.step").write_bytes(seed_step.read_bytes())

        edit_script = tmp_path / "edit.py"
        edit_script.write_text(
            """import cadquery as cq
try:
    open('/inputs/mutation.txt', 'w', encoding='utf-8').write('no')
except OSError:
    pass
else:
    raise RuntimeError('fixture input mount was writable')
result = cq.importers.importStep('/inputs/input.step').translate((5, 0, 0))
""",
            encoding="utf-8",
        )
        artifacts = cadquery_backend.compile_cadquery_to_artifacts(
            edit_script,
            tmp_path / "edit-out",
            readonly_input_dir=inputs,
        )

        assert artifacts.stl_path.with_name("output.step").stat().st_size > 0
        assert not (inputs / "mutation.txt").exists()

    def test_brep_metric_failure_is_warning_only(self, tmp_path, monkeypatch):
        script = tmp_path / "metric_failure.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cadquery_backend,
            "_step_mesh_volume_warning",
            lambda *_args: (_ for _ in ()).throw(ValueError("controlled failure")),
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(
            script, tmp_path / "out"
        )

        assert artifacts.stl_path.stat().st_size > 0
        assert artifacts.png_path.stat().st_size > 0
        assert artifacts.warnings[-1] == (
            "brep_mesh_volume: unavailable (ValueError: controlled failure)"
        )

    def test_show_shape_is_accepted(self, tmp_path):
        script = tmp_path / "shown.py"
        script.write_text(
            "import cadquery as cq\nshow(cq.Workplane('XY').cylinder(20, 5).val())\n",
            encoding="utf-8",
        )
        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")
        assert artifacts.stl_path.stat().st_size > 0

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("result = (\n", "SyntaxError"),
            ("import cadquery as cq\nvalue = cq.Workplane('XY').box(1, 1, 1)\n", "no result"),
            ("raise SystemExit(0)\n", "SystemExit"),
        ],
    )
    def test_candidate_defects_raise_compile_error(self, tmp_path, source, message):
        script = tmp_path / "bad.py"
        script.write_text(source, encoding="utf-8")
        with pytest.raises(render.CompileError, match=message):
            cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

    def test_infinite_loop_obeys_timeout(self, tmp_path, monkeypatch):
        script = tmp_path / "loop.py"
        script.write_text("while True:\n    pass\n", encoding="utf-8")
        monkeypatch.setenv(cadquery_backend.CADQUERY_TIMEOUT_ENV, "1")
        with pytest.raises(render.CompileError, match="timed out after 1s"):
            cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

    def test_secret_environment_is_scrubbed_and_host_file_is_hidden(self, tmp_path, monkeypatch):
        script = tmp_path / "probe.py"
        script.write_text(
            """import os
import cadquery as cq
try:
    leaked = os.environ["GH_TOKEN"]
except KeyError:
    leaked = None
try:
    open("/etc/hostname", encoding="utf-8").read()
except OSError:
    host_file_hidden = True
else:
    host_file_hidden = False
if leaked is not None:
    raise RuntimeError("secret leaked into entrant process")
if not host_file_hidden:
    raise RuntimeError("host file visible inside entrant sandbox")
result = cq.Workplane("XY").box(10, 10, 10)
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("GH_TOKEN", "atlas-mutation-secret")

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

        assert artifacts.stl_path.stat().st_size > 0
        assert "atlas-mutation-secret" not in "\n".join(artifacts.warnings)

    def test_direct_secret_probe_fails_closed(self, tmp_path, monkeypatch):
        script = tmp_path / "secret_probe.py"
        script.write_text(
            """import os
import cadquery as cq
token = os.environ["GH_TOKEN"]
result = cq.Workplane("XY").box(len(token), 10, 10)
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("GH_TOKEN", "atlas-mutation-secret")

        with pytest.raises(render.CompileError, match=r"KeyError\('GH_TOKEN'\)"):
            cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

    def test_direct_host_file_probe_fails_closed_independently(self, tmp_path):
        script = tmp_path / "host_file_probe.py"
        script.write_text(
            """import cadquery as cq
hostname = open("/etc/hostname", encoding="utf-8").read()
result = cq.Workplane("XY").box(len(hostname), 10, 10)
""",
            encoding="utf-8",
        )

        with pytest.raises(render.CompileError, match="FileNotFoundError"):
            cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

    def test_existing_output_directory_contents_are_not_mounted(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "host-secret.txt").write_text("controlled sentinel", encoding="utf-8")
        script = tmp_path / "output_probe.py"
        script.write_text(
            """import cadquery as cq
sentinel = open("/out/host-secret.txt", encoding="utf-8").read()
result = cq.Workplane("XY").box(len(sentinel), 10, 10)
""",
            encoding="utf-8",
        )

        with pytest.raises(render.CompileError, match="FileNotFoundError"):
            cadquery_backend.compile_cadquery_to_artifacts(script, out_dir)

    def test_worker_blocks_network_inside_the_filesystem_sandbox(self, tmp_path):
        script = tmp_path / "network.py"
        script.write_text(
            """import socket
import cadquery as cq
network_open = False
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.25)
    network_open = True
except OSError:
    pass
if network_open:
    raise RuntimeError("network unexpectedly reachable")
result = cq.Workplane("XY").box(10, 10, 10)
""",
            encoding="utf-8",
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

        assert "network_isolation: unavailable" not in artifacts.warnings


@pytestmark_build123d
class TestRealBuild123dAlias:
    def test_build123d_part_uses_the_same_step_stl_png_pipeline(self, tmp_path):
        script = tmp_path / "build123d_part.py"
        script.write_text(
            "from build123d import Box\nresult = Box(60, 40, 20)\n",
            encoding="utf-8",
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(
            script, tmp_path / "out"
        )

        assert artifacts.stl_path.stat().st_size > 0
        assert artifacts.png_path.stat().st_size > 0
        assert artifacts.stl_path.with_name("output.step").stat().st_size > 0

    def test_build123d_show_part_is_accepted(self, tmp_path):
        script = tmp_path / "build123d_show.py"
        script.write_text(
            "import build123d as b3d\nshow(b3d.Cylinder(5, 20))\n",
            encoding="utf-8",
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(
            script, tmp_path / "out"
        )
        assert artifacts.stl_path.stat().st_size > 0

    def test_build123d_import_requires_a_part_result(self, tmp_path):
        script = tmp_path / "not_a_part.py"
        script.write_text("import build123d\nresult = 42\n", encoding="utf-8")

        with pytest.raises(render.CompileError, match="must be a build123d Part"):
            cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")


class TestDriverVolumeParsing:
    def test_reads_the_marker_line(self):
        out = "noise\nCADQUERY_DRIVER_VOLUME: 62296.6\nCADQUERY_DRIVER_OK\n"
        assert cadquery_backend._driver_volume(out) == 62296.6

    def test_missing_or_garbled_marker_is_none(self):
        assert cadquery_backend._driver_volume("CADQUERY_DRIVER_OK\n") is None
        assert cadquery_backend._driver_volume("CADQUERY_DRIVER_VOLUME: nope\n") is None

    def test_mesh_warning_uses_a_given_brep_volume_without_reading_the_step(self, tmp_path):
        import trimesh

        stl = tmp_path / "b.stl"
        trimesh.creation.box(extents=[10, 10, 10]).export(stl.as_posix())
        warning = cadquery_backend._step_mesh_volume_warning(
            tmp_path / "does-not-exist.step", stl, 1000.0
        )
        assert warning == "brep_mesh_volume: brep_mm3=1000.000; mesh_mm3=1000.000; relative_delta=0.000000"
