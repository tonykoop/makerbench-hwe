"""Tests for the sandboxed CadQuery arena compiler (#752)."""

from __future__ import annotations

import os
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


def test_network_namespace_probe_reports_unavailable_when_unshare_missing(monkeypatch):
    monkeypatch.setattr(cadquery_backend.shutil, "which", lambda _name: None)
    assert cadquery_backend._unprivileged_network_namespace_available({}) is False


def test_network_namespace_probe_reports_unavailable_when_probe_fails(monkeypatch):
    monkeypatch.setattr(cadquery_backend.shutil, "which", lambda _name: "/usr/bin/unshare")
    monkeypatch.setattr(
        cadquery_backend.subprocess,
        "run",
        lambda *args, **kwargs: _completed(returncode=1, stderr="operation not permitted"),
    )
    assert cadquery_backend._unprivileged_network_namespace_available({}) is False


def test_missing_cadquery_is_an_environment_error(tmp_path, monkeypatch):
    script = tmp_path / "entrant.py"
    script.write_text("result = None\n", encoding="utf-8")
    monkeypatch.setattr(
        cadquery_backend,
        "_unprivileged_network_namespace_available",
        lambda _env: False,
    )

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

    def test_secret_environment_is_scrubbed_before_host_file_probe(self, tmp_path, monkeypatch):
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
    pass
if leaked is not None:
    raise RuntimeError("secret leaked into entrant process")
result = cq.Workplane("XY").box(10, 10, 10)
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("GH_TOKEN", "atlas-mutation-secret")

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

        assert artifacts.stl_path.stat().st_size > 0
        assert "atlas-mutation-secret" not in "\n".join(artifacts.warnings)

    def test_direct_secret_and_host_file_probe_fails_closed(self, tmp_path, monkeypatch):
        script = tmp_path / "direct_probe.py"
        script.write_text(
            """import os
import cadquery as cq
token = os.environ["GH_TOKEN"]
hostname = open("/etc/hostname", encoding="utf-8").read()
result = cq.Workplane("XY").box(len(token), len(hostname), 10)
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("GH_TOKEN", "atlas-mutation-secret")

        with pytest.raises(render.CompileError, match=r"KeyError\('GH_TOKEN'\)"):
            cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

    def test_worker_uses_network_namespace_or_records_unavailable(self, tmp_path):
        namespace_available = cadquery_backend._unprivileged_network_namespace_available(
            cadquery_backend._scrub_environment(os.environ)
        )
        script = tmp_path / "network.py"
        script.write_text(
            (
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
"""
                if namespace_available
                else "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"
            ),
            encoding="utf-8",
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

        assert (
            namespace_available
            or "network_isolation: unavailable" in artifacts.warnings
        )

    def test_unavailable_network_namespace_is_disclosed(self, tmp_path, monkeypatch):
        script = tmp_path / "fallback.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cadquery_backend,
            "_unprivileged_network_namespace_available",
            lambda _env: False,
        )

        artifacts = cadquery_backend.compile_cadquery_to_artifacts(script, tmp_path / "out")

        assert "network_isolation: unavailable" in artifacts.warnings
