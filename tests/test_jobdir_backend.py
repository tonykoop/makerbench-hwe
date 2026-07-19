"""Tests for the shared WSL<->Windows job-dir protocol (#627)."""

from __future__ import annotations

import json

import pytest

from makerbench import jobdir_backend as jd
from makerbench import render


class _FakeClock:
    """Deterministic clock: each call advances by ``step`` seconds."""

    def __init__(self, step: float = 1.0):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class TestJobsRoot:
    def test_defaults_to_relative_jobs_dir(self, monkeypatch):
        monkeypatch.delenv(jd.JOBS_ROOT_ENV, raising=False)
        assert jd.jobs_root() == jd.DEFAULT_JOBS_ROOT

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv(jd.JOBS_ROOT_ENV, str(tmp_path / "custom-jobs"))
        assert jd.jobs_root() == tmp_path / "custom-jobs"


class TestJobdirHandoffAvailable:
    def test_false_when_no_windows_bridge(self, monkeypatch, tmp_path):
        monkeypatch.setattr(jd.Path, "is_dir", lambda self: False)
        assert jd.jobdir_handoff_available(root=tmp_path / "jobs") is False

    def test_true_when_bridge_present_and_root_creatable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(jd.Path, "is_dir", lambda self: True)
        root = tmp_path / "jobs"
        assert jd.jobdir_handoff_available(root=root) is True
        assert root.exists()


class TestCreateJob:
    def test_creates_expected_layout_and_pending_status(self, tmp_path):
        source = tmp_path / "entrant.vba"
        source.write_text("Sub BuildPart()\nEnd Sub\n", encoding="utf-8")
        root = tmp_path / "jobs"

        jdir = jd.create_job("trial-1", source, backend="solidworks", root=root)

        assert jdir == root / "trial-1"
        assert (jdir / "input").is_dir()
        assert (jdir / "artifacts").is_dir()
        entrant_copy = jdir / "input" / "entrant.vba"
        assert entrant_copy.read_text(encoding="utf-8") == "Sub BuildPart()\nEnd Sub\n"

        payload = json.loads((jdir / "status.json").read_text(encoding="utf-8"))
        assert payload["status"] == "pending"
        assert payload["trial_id"] == "trial-1"
        assert payload["backend"] == "solidworks"
        assert payload["stl_path"] is None
        assert payload["error"] is None

    def test_source_filename_override(self, tmp_path):
        source = tmp_path / "whatever.txt"
        source.write_text("def build(app, design):\n    pass\n", encoding="utf-8")
        jdir = jd.create_job(
            "trial-2", source, backend="fusion", root=tmp_path / "jobs", source_filename="entrant.py"
        )
        assert (jdir / "input" / "entrant.py").exists()


class TestWriteAndReadStatus:
    def test_read_status_none_when_missing(self, tmp_path):
        assert jd.read_status(tmp_path / "nope") is None

    def test_write_then_read_roundtrip(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-3"
        jd.write_status(jdir, status="running", trial_id="trial-3", backend="fusion")
        payload = jd.read_status(jdir)
        assert payload["status"] == "running"
        assert payload["trial_id"] == "trial-3"

    def test_invalid_status_raises(self, tmp_path):
        with pytest.raises(ValueError, match="invalid job status"):
            jd.write_status(tmp_path / "jobs" / "x", status="bogus", trial_id="x")

    def test_read_status_tolerates_unreadable_json(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-4"
        jdir.mkdir(parents=True)
        (jdir / "status.json").write_text("{not json", encoding="utf-8")
        assert jd.read_status(jdir) is None


class TestPollJob:
    def test_returns_payload_on_done(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-5"
        stl = jdir / "artifacts" / "output.stl"
        stl.parent.mkdir(parents=True)
        stl.write_text("solid fake\nendsolid fake\n", encoding="utf-8")

        clock = _FakeClock()
        calls = {"n": 0}

        def sleep_fn(seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                jd.write_status(
                    jdir,
                    status="running",
                    trial_id="trial-5",
                    backend="solidworks",
                )
            elif calls["n"] == 2:
                jd.write_status(
                    jdir,
                    status="done",
                    trial_id="trial-5",
                    backend="solidworks",
                    stl_path=str(stl),
                    png_path=None,
                    units="mm",
                )

        payload = jd.poll_job(
            jdir, timeout_s=60, poll_interval_s=1, sleep_fn=sleep_fn, clock_fn=clock
        )
        assert payload["status"] == "done"
        assert payload["stl_path"] == str(stl)
        assert calls["n"] == 2

    def test_raises_compile_error_on_error_status(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-6"
        jd.write_status(
            jdir, status="error", trial_id="trial-6", backend="fusion", error="entrant script crashed"
        )
        with pytest.raises(render.CompileError, match="entrant script crashed"):
            jd.poll_job(jdir, timeout_s=60, sleep_fn=lambda s: None, clock_fn=_FakeClock())

    def test_raises_compile_error_on_timeout_without_real_sleep(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-7"
        jd.write_status(jdir, status="pending", trial_id="trial-7", backend="fusion")

        clock = _FakeClock(step=50.0)  # advances well past a small timeout every call
        slept = []

        with pytest.raises(render.CompileError, match="did not complete within"):
            jd.poll_job(
                jdir,
                timeout_s=100,
                poll_interval_s=1,
                sleep_fn=lambda s: slept.append(s),
                clock_fn=clock,
            )
        # No real waiting happened; sleep_fn was called at most a couple times
        # before the fake clock pushed elapsed past timeout_s.
        assert len(slept) <= 3

    def test_done_with_missing_stl_file_is_compile_error(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-8"
        jd.write_status(
            jdir,
            status="done",
            trial_id="trial-8",
            backend="solidworks",
            stl_path=str(tmp_path / "does-not-exist.stl"),
        )
        with pytest.raises(render.CompileError, match="does not exist"):
            jd.poll_job(jdir, timeout_s=10, sleep_fn=lambda s: None, clock_fn=_FakeClock())

    def test_missing_status_file_eventually_times_out(self, tmp_path):
        jdir = tmp_path / "jobs" / "trial-9"  # never created
        clock = _FakeClock(step=10.0)
        with pytest.raises(render.CompileError, match="did not complete within"):
            jd.poll_job(
                jdir, timeout_s=5, poll_interval_s=1, sleep_fn=lambda s: None, clock_fn=clock
            )
