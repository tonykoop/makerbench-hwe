"""
Tests for arena.render — render adapter stub, auto-fail path.

All tests run offline.  The key contract: if openscad is absent, render_scad()
must return RenderResult(status=OPENSCAD_NOT_FOUND) without raising.
"""

from __future__ import annotations

import pytest

from arena.render import RenderResult, RenderStatus, render_scad


MINIMAL_SCAD = """\
// Minimal test .scad
sphere(r=10);
"""


class TestRenderAutoFail:
    def test_openscad_absent_returns_not_found(self, monkeypatch):
        """Core auto-fail contract: missing openscad → OPENSCAD_NOT_FOUND."""
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = render_scad(MINIMAL_SCAD)
        assert result.status == RenderStatus.OPENSCAD_NOT_FOUND

    def test_openscad_absent_does_not_raise(self, monkeypatch):
        """render_scad must never raise; it always returns a RenderResult."""
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = render_scad(MINIMAL_SCAD)  # must not raise
        assert isinstance(result, RenderResult)

    def test_openscad_absent_is_objective_fail(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = render_scad(MINIMAL_SCAD)
        assert result.is_objective_fail is True

    def test_openscad_absent_no_png_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = render_scad(MINIMAL_SCAD)
        assert result.png_path is None

    def test_openscad_absent_stderr_descriptive(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = render_scad(MINIMAL_SCAD)
        assert result.stderr  # must have a non-empty explanation

    def test_ok_status_is_not_objective_fail(self):
        """RenderStatus.OK must NOT be an auto-fail."""
        ok_result = RenderResult(status=RenderStatus.OK)
        assert ok_result.is_objective_fail is False

    @pytest.mark.parametrize(
        "status",
        [
            RenderStatus.OPENSCAD_NOT_FOUND,
            RenderStatus.COMPILE_ERROR,
            RenderStatus.TIMEOUT,
            RenderStatus.UNKNOWN_ERROR,
        ],
    )
    def test_non_ok_statuses_are_objective_fails(self, status):
        result = RenderResult(status=status)
        assert result.is_objective_fail is True


class TestRenderWithFakeOpenscad:
    """Test render_scad with a fake openscad binary that succeeds."""

    def test_successful_render_returns_ok(self, monkeypatch, tmp_path):
        """Simulate openscad present and succeeding (returncode=0, png written)."""
        import subprocess

        png_path = tmp_path / "model.png"

        def fake_which(name):
            return "/usr/bin/openscad"

        def fake_run(cmd, **kwargs):
            # Write the expected PNG so the check passes
            output_flag_idx = cmd.index("-o")
            out_file = cmd[output_flag_idx + 1]
            import pathlib
            pathlib.Path(out_file).write_bytes(b"\x89PNG\r\n")
            proc = subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            return proc

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = render_scad(MINIMAL_SCAD, output_dir=tmp_path)
        assert result.status == RenderStatus.OK
        assert not result.is_objective_fail

    def test_compile_error_returns_compile_error(self, monkeypatch, tmp_path):
        """Simulate openscad present but returning non-zero exit code."""
        import subprocess

        def fake_which(name):
            return "/usr/bin/openscad"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, returncode=1, stdout="", stderr="ERROR: parser error"
            )

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = render_scad(MINIMAL_SCAD, output_dir=tmp_path)
        assert result.status == RenderStatus.COMPILE_ERROR
        assert result.is_objective_fail
        assert "ERROR" in result.stderr

    def test_timeout_returns_timeout(self, monkeypatch, tmp_path):
        """Simulate openscad timing out."""
        import subprocess

        def fake_which(name):
            return "/usr/bin/openscad"

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=120)

        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.setattr("subprocess.run", fake_run)

        result = render_scad(MINIMAL_SCAD, output_dir=tmp_path, timeout_seconds=120)
        assert result.status == RenderStatus.TIMEOUT
        assert result.is_objective_fail
