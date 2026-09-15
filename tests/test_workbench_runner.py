"""Workbench service and job runner (#788 W3a), tested without HTTP.

Hermetic tests replace the detached launch with an inline fake that writes
the same files the real job writes. ``TestRealJob`` spawns the real
``arena workbench-job`` subprocess inside the real Bubblewrap sandbox; it is
skipped only when the sandbox is unavailable, and ``MAKERBENCH_REQUIRE_SANDBOX=1``
(CI) turns that skip into a failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import typer.main
from typer.testing import CliRunner

from makerbench import render, scad_sandbox
from makerbench.arena_studio import workbench as wb
from makerbench.cli import app as cli_app
from makerbench.cli_arena import arena_app
from makerbench.redaction import find_host_paths
from makerbench.workbench_store import Conflict, NotFound, TooLarge, WorkbenchError

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()
if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start", pytrace=False)
needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

CUBE = "w_mm = 10;\ncube(w_mm);\n"
DEAD_PID = 2_147_483_647


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "schema": "makerbench-code-cad-arena-registry-v1",
        "instruments": [
            {"id": "boxolin", "display_name": "Boxolin", "family": "strings", "task_kind": "single_part",
             "envelope_mm": [100, 100, 100], "min_bodies": 1, "repo_path": "strings/boxolin"},
            {"id": "sneaky", "display_name": "Sneaky", "family": "strings", "task_kind": "single_part",
             "envelope_mm": [10, 10, 10], "repo_path": "../outside"},
            {"id": "hidden", "display_name": "Hidden", "family": "strings", "task_kind": "single_part",
             "envelope_mm": [10, 10, 10], "repo_path": "private/hidden"},
            {"id": "norepo", "display_name": "No repo", "family": "strings", "task_kind": "single_part",
             "envelope_mm": [10, 10, 10]},
        ],
    }), encoding="utf-8")
    return path


@pytest.fixture
def instruments_root(tmp_path: Path) -> Path:
    root = tmp_path / "instruments"
    cad = root / "strings" / "boxolin" / "CAD"
    cad.mkdir(parents=True)
    (cad / "boxolin.scad").write_text(CUBE, encoding="utf-8")
    (cad / "notes.md").write_text("not a master\n", encoding="utf-8")
    (cad / "private-notes.scad").write_text("cube(1);\n", encoding="utf-8")
    secret = tmp_path / "outside" / "cad"
    secret.mkdir(parents=True)
    (secret / "secret.scad").write_text("cube(999);\n", encoding="utf-8")
    hidden = root / "private" / "hidden" / "cad"
    hidden.mkdir(parents=True)
    (hidden / "oracle.scad").write_text("cube(7);\n", encoding="utf-8")
    os.symlink(secret / "secret.scad", cad / "linked.scad")
    return root


@pytest.fixture
def fake_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "code_cad_arena" / "round9"
    gen = run_dir / "gen" / "boxolin__seed0__rep0__stub-a"
    gen.mkdir(parents=True)
    (gen / "candidate.scad").write_text("size = 30;\ncube(size);\n", encoding="utf-8")
    outside = tmp_path / "outside.scad"
    outside.write_text("cube(1);\n", encoding="utf-8")
    run_log = {
        "schema": "makerbench-code-cad-arena-run-v1",
        "config": {"model_ids": ["stub-a"], "instrument_ids": ["boxolin"], "backend": "openscad"},
        "trials": [
            {"trial_id": "boxolin__seed0__rep0__stub-a", "model_id": "stub-a", "instrument_id": "boxolin", "seed": 0, "rep": 0,
             "status": "scored", "result": {"render_ok": True, "gen": {"scad_path": str(gen / "candidate.scad")}}},
            {"trial_id": "boxolin__seed0__rep0__evil", "model_id": "evil", "instrument_id": "boxolin", "seed": 0, "rep": 0,
             "status": "scored", "result": {"render_ok": True, "gen": {"scad_path": str(outside)}}},
            {"trial_id": "boxolin__seed0__rep0__nosrc", "model_id": "nosrc", "instrument_id": "boxolin", "seed": 0, "rep": 0,
             "status": "auto_fail", "result": {"render_ok": False}},
        ],
        "summary": {"counts": {"scored": 2, "auto_fail": 1}},
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    return run_dir


@pytest.fixture
def service(tmp_path: Path, registry: Path, instruments_root: Path) -> wb.WorkbenchService:
    return wb.WorkbenchService(repo_root=tmp_path, registry_path=registry, instruments_root=instruments_root)


class _FakeProc:
    """A never-finishing process handle whose pid cannot be signalled."""

    pid = DEAD_PID

    def poll(self):
        return None

    def wait(self, timeout=None):
        return None


def _fake_launch(service: wb.WorkbenchService, *, fail: bool = False, hang: bool = False):
    launched: list[tuple[str, str]] = []

    def launch(design_id: str, draft_id: str) -> None:
        launched.append((design_id, draft_id))
        ddir = service.store.draft_dir(design_id, draft_id)
        (ddir / "job.log").write_text("=== fake job ===\ncompiling in the sandbox ...\n", encoding="utf-8")
        if hang:
            service._processes[(design_id, draft_id)] = _FakeProc()  # type: ignore[assignment]
            service.store.update_draft_job(design_id, draft_id, status="running", pid=DEAD_PID, started_at=wb._now())
            return
        service.store.update_draft_job(design_id, draft_id, status="running", pid=os.getpid(), started_at=wb._now())
        art = ddir / "artifacts"
        art.mkdir(exist_ok=True)
        if fail:
            service.store.update_draft_job(design_id, draft_id, status="failed", exit_code=1, finished_at=wb._now(), error="ERROR: Parser error in line 1")
            return
        (art / "output.stl").write_text("solid x\nfacet normal 0 0 0\nendsolid\n", encoding="utf-8")
        (art / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
        (ddir / "objective.json").write_text(json.dumps({"render_ok": True, "status": "scored", "objective": {"declared": True, "objective_pass_rate": 1.0}, "sandbox": {"kind": "bwrap", "verified": True, "xvfb": True}}), encoding="utf-8")
        service.store.update_draft_job(design_id, draft_id, status="succeeded", exit_code=0, finished_at=wb._now())

    service._launch = launch  # type: ignore[method-assign]
    return launched


def _snapshot(root: Path) -> dict:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file() and p.suffix != ".lock"}


def _origin(service, **origin) -> tuple[str, str]:
    created = service.create_design(origin=origin or {"blank": {"backend": "openscad", "instrument_id": "boxolin"}})
    did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
    rev = service.save_revision(did, draft_id=jid, note="origin")
    return did, rev["rev_id"]


# --- origins ---------------------------------------------------------------------


class TestOrigins:
    def test_blank_creates_design_and_compiles_the_origin_draft(self, service, tmp_path):
        launched = _fake_launch(service)
        created = service.create_design(origin={"blank": {"backend": "openscad", "instrument_id": "boxolin"}}, title="Fresh")
        design, draft = created["design"], created["draft"]
        assert launched == [(design["design_id"], draft["draft_id"])]
        assert design["title"] == "Fresh" and design["origin"] == {"kind": "blank", "instrument_id": "boxolin"}
        assert draft["job"]["status"] == "succeeded" and draft["queue_position"] is None
        assert "size_mm = 20" in service.store.draft_source(design["design_id"], draft["draft_id"])
        assert tmp_path.as_posix() not in json.dumps(created) and not find_host_paths(json.dumps(created))

    def test_master_origin_from_uppercase_cad_dir(self, service, tmp_path):
        _fake_launch(service)
        assert service.master_files("boxolin") == ["boxolin.scad", "private-notes.scad"]
        created = service.create_design(origin={"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        assert service.store.draft_source(did, jid) == CUBE
        assert created["design"]["origin"] == {"kind": "master", "instrument_id": "boxolin", "repo_rel_path": "strings/boxolin/CAD/boxolin.scad", "file": "boxolin.scad"}
        assert (tmp_path / "instruments" / "strings" / "boxolin" / "CAD" / "boxolin.scad").read_text() == CUBE

    @pytest.mark.parametrize("origin, exc, needle", [
        ({"master": {"instrument_id": "boxolin", "file": "linked.scad"}}, NotFound, "unknown master"),
        ({"master": {"instrument_id": "boxolin", "file": "../cad/boxolin.scad"}}, NotFound, "unknown master"),
        ({"master": {"instrument_id": "boxolin", "file": "notes.md"}}, WorkbenchError, ".scad or .py"),
        ({"master": {"instrument_id": "boxolin", "file": "nope.scad"}}, NotFound, "unknown master"),
        ({"master": {"instrument_id": "boxolin", "file": ".hidden.scad"}}, NotFound, "unknown master"),
        ({"master": {"instrument_id": "boxolin", "file": "private-notes.scad"}}, NotFound, "unknown master"),
        ({"master": {"instrument_id": "sneaky", "file": "secret.scad"}}, NotFound, "unknown instrument"),
        ({"master": {"instrument_id": "hidden", "file": "oracle.scad"}}, NotFound, "unknown instrument"),
        ({"master": {"instrument_id": "norepo", "file": "x.scad"}}, WorkbenchError, "no repo_path"),
        ({"master": {"instrument_id": "Nope", "file": "x.scad"}}, NotFound, "unknown instrument"),
        ({"trial": {"run_id": "round9", "trial_id": "boxolin__seed0__rep0__evil"}}, NotFound, "not inside its run"),
        ({"trial": {"run_id": "round9", "trial_id": "boxolin__seed0__rep0__nosrc"}}, WorkbenchError, "no generated source"),
        ({"trial": {"run_id": "round9", "trial_id": "nope"}}, NotFound, "unknown trial"),
        ({"trial": {"run_id": "round9", "trial_id": "../x"}}, NotFound, "unknown trial"),
        ({"blank": {"backend": "blender"}}, WorkbenchError, "backend"),
        ({"blank": {"backend": "openscad", "instrument_id": "Bad Id"}}, WorkbenchError, "instrument_id"),
        ({}, WorkbenchError, "one of"),
    ])
    def test_refused_origins_create_nothing(self, service, tmp_path, fake_run, origin, exc, needle):
        launched = _fake_launch(service)
        root = tmp_path / "runs" / "workbench"
        before = _snapshot(root)
        with pytest.raises(exc, match=needle):
            service.create_design(origin=origin, run_dir=fake_run if "trial" in origin else None)
        assert _snapshot(root) == before
        assert launched == [] and service.store.list_designs() == []

    def test_trial_origin_copies_the_candidate_and_never_writes_the_run(self, service, tmp_path, fake_run):
        _fake_launch(service)
        before = _snapshot(fake_run)
        created = service.create_design(origin={"trial": {"run_id": "round9", "trial_id": "boxolin__seed0__rep0__stub-a"}}, run_dir=fake_run)
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        assert service.store.draft_source(did, jid) == "size = 30;\ncube(size);\n"
        assert created["design"]["origin"] == {"kind": "trial", "run_id": "round9", "trial_id": "boxolin__seed0__rep0__stub-a", "model_id": "stub-a", "instrument_id": "boxolin"}
        assert _snapshot(fake_run) == before

    def test_masters_need_an_instruments_root(self, tmp_path, registry):
        service = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry)
        with pytest.raises(WorkbenchError, match="--instruments-root"):
            service.master_files("boxolin")
        with pytest.raises(WorkbenchError, match="--instruments-root"):
            service.create_design(origin={"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}})


# --- drafts -----------------------------------------------------------------------


class TestDrafts:
    def test_edit_then_save_then_parameters(self, service):
        _fake_launch(service)
        did, r0 = _origin(service)
        draft = service.start_edit(did, parent_rev_id=r0, source="size_mm = 40;\ncube(size_mm);\n")
        assert draft["job"]["status"] == "succeeded" and draft["kind"] == "edit"
        r1 = service.save_revision(did, draft_id=draft["draft_id"], note="bigger")
        assert r1["seq"] == 2 and r1["parent_rev_id"] == r0 and r1["compile"]["sandbox"]["kind"] == "bwrap"
        assert [p["name"] for p in service.parameters(did, r1["rev_id"])["parameters"]] == ["size_mm"]
        applied = service.start_params(did, parent_rev_id=r1["rev_id"], values={"size_mm": 25})
        assert applied["kind"] == "parameters" and applied["editor"]["changed"] == {"size_mm": [40, 25]}
        assert "size_mm = 25;" in service.store.draft_source(did, applied["draft_id"])
        with pytest.raises(Conflict, match="no parameter changed"):
            service.start_params(did, parent_rev_id=r1["rev_id"], values={"size_mm": 40})
        with pytest.raises(WorkbenchError):
            service.start_params(did, parent_rev_id=r1["rev_id"], values={"nope": 1})
        with pytest.raises(WorkbenchError, match="non-empty"):
            service.start_params(did, parent_rev_id=r1["rev_id"], values={})
        with pytest.raises(WorkbenchError, match="at most"):
            service.start_params(did, parent_rev_id=r1["rev_id"], values={f"p{i}": i for i in range(wb.MAX_PARAMS + 1)})
        with pytest.raises(Conflict, match="parent_rev_id"):
            service.start_params(did, parent_rev_id=None, values={"size_mm": 1})
        with pytest.raises(TooLarge):
            service.start_edit(did, parent_rev_id=r1["rev_id"], source="x" * (256 * 1024 + 1))

    def test_failed_compile_cannot_be_saved(self, service):
        _fake_launch(service, fail=True)
        created = service.create_design(origin={"blank": {"backend": "openscad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        assert created["draft"]["job"]["status"] == "failed" and "Parser error" in created["draft"]["job"]["error"]
        with pytest.raises(Conflict, match="only a succeeded"):
            service.save_revision(did, draft_id=jid)

    def test_get_design_refreshes_drafts(self, service):
        _fake_launch(service, hang=True)
        created = service.create_design(origin={"blank": {"backend": "openscad"}})
        payload = service.get_design(created["design"]["design_id"])
        assert payload["drafts"][0]["job"]["status"] == "running" and payload["revisions"] == []


# --- limits, cancel, restart -----------------------------------------------------


def _busy_design(service) -> tuple[str, str, str]:
    """A design with a saved origin and one hanging (running) draft."""

    created = service.create_design(origin={"blank": {"backend": "openscad", "instrument_id": "boxolin"}})
    did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
    ddir = service.store.draft_dir(did, jid)
    (ddir / "artifacts").mkdir(exist_ok=True)
    (ddir / "artifacts" / "output.stl").write_text("solid x\nendsolid\n", encoding="utf-8")
    service._processes.pop((did, jid), None)
    service.store.update_draft_job(did, jid, status="succeeded", exit_code=0, finished_at=wb._now())
    rev = service.save_revision(did, draft_id=jid)["rev_id"]
    running = service.start_edit(did, parent_rev_id=rev, source="cube(1);\n")
    assert running["job"]["status"] == "running"
    return did, rev, running["draft_id"]


class TestLimits:
    def test_queue_cap_refuses_before_creating_a_draft(self, service, tmp_path):
        launched = _fake_launch(service, hang=True)
        service.max_running_per_server = 1
        service.max_queued = 2
        did, rev, _running = _busy_design(service)
        a = service.start_edit(did, parent_rev_id=rev, source="cube(2);\n")
        b = service.start_edit(did, parent_rev_id=rev, source="cube(3);\n")
        assert a["job"]["status"] == "queued" and a["queue_position"] == 1
        assert b["job"]["status"] == "queued" and b["queue_position"] == 2
        before = _snapshot(tmp_path / "runs" / "workbench")
        with pytest.raises(wb.QueueFull, match="queue full"):
            service.start_edit(did, parent_rev_id=rev, source="cube(4);\n")
        assert _snapshot(tmp_path / "runs" / "workbench") == before
        assert len(list((tmp_path / "runs" / "workbench" / did / "drafts").iterdir())) == 4
        assert len(launched) == 2  # the origin and the running draft; nothing queued was launched

    def test_one_running_job_per_design(self, service):
        launched = _fake_launch(service, hang=True)
        service.max_running_per_server = 5
        did_a, rev_a, _ = _busy_design(service)
        did_b, _rev_b, _ = _busy_design(service)
        queued = service.start_edit(did_a, parent_rev_id=rev_a, source="cube(3);\n")
        assert queued["job"]["status"] == "queued"
        assert len(launched) == 4  # two origins, two running drafts; a's second draft waits

    def test_server_cap_and_cancel_frees_a_slot(self, service):
        launched = _fake_launch(service, hang=True)
        service.max_running_per_server = 1
        did, rev, running = _busy_design(service)
        queued = service.start_edit(did, parent_rev_id=rev, source="cube(3);\n")
        assert queued["job"]["status"] == "queued"
        cancelled = service.cancel(did, running)
        assert cancelled["job"]["status"] == "cancelled"
        assert service.refresh_draft(did, queued["draft_id"])["job"]["status"] == "running"
        assert len(launched) == 3
        with pytest.raises(Conflict, match="already cancelled"):
            service.cancel(did, running)

    def test_stream_log_ends_with_the_final_status(self, service):
        _fake_launch(service)
        created = service.create_design(origin={"blank": {"backend": "openscad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        lines = list(service.stream_log(did, jid, follow=False))
        assert lines[0] == 'data: "=== fake job ==="\n\n'
        assert lines[-1] == 'event: end\ndata: "succeeded"\n\n'
        with pytest.raises(NotFound):
            list(service.stream_log(did, "j-nope", follow=False))


class TestRestart:
    def test_dead_pid_is_interrupted_and_queued_drafts_resume(self, tmp_path, registry, monkeypatch):
        launched: list[tuple[str, str]] = []

        def class_fake(self, design_id, draft_id):
            launched.append((design_id, draft_id))
            self._processes[(design_id, draft_id)] = _FakeProc()
            self.store.update_draft_job(design_id, draft_id, status="running", pid=DEAD_PID, started_at=wb._now())

        monkeypatch.setattr(wb.WorkbenchService, "_launch", class_fake)
        first = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry, max_running_per_server=1)
        a = first.create_design(origin={"blank": {"backend": "openscad"}})
        b = first.create_design(origin={"blank": {"backend": "openscad"}})
        assert b["draft"]["job"]["status"] == "queued"
        launched.clear()
        second = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry, max_running_per_server=1)
        da = second.refresh_draft(a["design"]["design_id"], a["draft"]["draft_id"])
        assert da["job"]["status"] == "interrupted" and "restarted" in da["job"]["error"]
        db = second.refresh_draft(b["design"]["design_id"], b["draft"]["draft_id"])
        assert db["job"]["status"] == "running" and launched == [(b["design"]["design_id"], b["draft"]["draft_id"])]

    def test_live_pid_survives_restart_and_can_be_cancelled(self, tmp_path, registry):
        first = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry)
        _fake_launch(first, hang=True)
        created = first.create_design(origin={"blank": {"backend": "openscad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        ddir = first.store.draft_dir(did, jid)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "workbench-job", str(ddir)], start_new_session=True)
        try:
            first.store.update_draft_job(did, jid, pid=process.pid)
            second = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry)
            assert second.refresh_draft(did, jid)["job"]["status"] == "running"
            assert second.cancel(did, jid)["job"]["status"] == "cancelled"
            process.wait(timeout=5)
            assert process.returncode != 0
        finally:
            if process.poll() is None:
                process.kill()

    def test_cancel_survives_a_concurrent_status_poll(self, tmp_path, registry, monkeypatch):
        """The browser polls status every second. A poll that lands between
        the kill and the status write must not turn the draft ``interrupted``."""

        service = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry)
        _fake_launch(service, hang=True)
        created = service.create_design(origin={"blank": {"backend": "openscad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]

        class _Exits(_FakeProc):
            def __init__(self):
                self.killed = False

            def poll(self):
                return -15 if self.killed else None

        proc = _Exits()
        service._processes[(did, jid)] = proc  # type: ignore[assignment]

        def kill_then_poll(pid):
            proc.killed = True
            # the concurrent poll: the process is gone, what does it conclude?
            assert service.refresh_draft(did, jid)["job"]["status"] == "cancelled"

        monkeypatch.setattr(wb.WorkbenchService, "_kill_group", staticmethod(kill_then_poll))
        assert service.cancel(did, jid)["job"]["status"] == "cancelled"
        assert service.refresh_draft(did, jid)["job"]["status"] == "cancelled"

    def test_refresh_never_overwrites_a_finished_status_with_interrupted(self, tmp_path, registry, monkeypatch):
        """The other interleaving: the poll read `running`, then cancel wrote
        `cancelled` and dropped the process handle, then the poll saw a dead pid."""

        service = wb.WorkbenchService(repo_root=tmp_path, registry_path=registry)
        _fake_launch(service, hang=True)
        created = service.create_design(origin={"blank": {"backend": "openscad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        service._processes.pop((did, jid))  # the handle is gone, as after cancel's pop
        real_read = service.store.read_draft
        calls = {"n": 0}

        def racy_read(design_id, draft_id):
            calls["n"] += 1
            payload = real_read(design_id, draft_id)
            if calls["n"] == 1:
                # first read: still running; then cancel lands
                service.store.update_draft_job(design_id, draft_id, status="cancelled", finished_at=wb._now(), error="cancelled")
            return payload

        monkeypatch.setattr(service.store, "read_draft", racy_read)
        assert service.refresh_draft(did, jid)["job"]["status"] == "cancelled"
        assert real_read(did, jid)["job"]["status"] == "cancelled"

    def test_kill_group_never_signals_our_own_group(self, monkeypatch):
        sent: list = []
        own = os.getpgid(0)
        monkeypatch.setattr(wb.os, "killpg", lambda pgid, sig: sent.append(("pg", pgid, sig)))
        monkeypatch.setattr(wb.os, "kill", lambda pid, sig: sent.append(("pid", pid, sig)) if sig else None)
        monkeypatch.setattr(wb.os, "getpgid", lambda pid: own)
        wb.WorkbenchService._kill_group(4242)
        assert all(kind == "pid" for kind, _, _ in sent) and sent


# --- the job body ----------------------------------------------------------------------


class TestRunJob:
    def _draft(self, tmp_path, instrument="boxolin"):
        from makerbench.workbench_store import WorkbenchStore

        store = WorkbenchStore(tmp_path / "runs" / "workbench")
        design = store.create_design(instrument_id=instrument, backend="openscad", title="", origin={"kind": "blank"})
        draft = store.create_draft(design["design_id"], parent_rev_id=None, source=CUBE, editor={"kind": "human"})
        return store, design["design_id"], draft["draft_id"], store.draft_dir(design["design_id"], draft["draft_id"])

    def test_unavailable_sandbox_is_a_failed_draft_and_no_host_compile(self, tmp_path, registry, monkeypatch):
        store, did, jid, ddir = self._draft(tmp_path)
        launched: list = []
        monkeypatch.setattr(scad_sandbox, "sandbox_available", lambda: False)
        monkeypatch.setattr(render, "_run", lambda *a, **k: launched.append(("host", a)))
        monkeypatch.setattr(scad_sandbox.subprocess, "run", lambda *a, **k: launched.append(("sandbox", a)))
        assert wb.run_job(ddir, registry) == 2
        job = store.read_draft(did, jid)["job"]
        assert job["status"] == "failed" and job["error"].startswith("sandbox_unavailable")
        assert launched == [] and not (ddir / "artifacts" / "output.stl").exists()

    def test_run_job_writes_params_objective_and_status(self, tmp_path, registry, monkeypatch):
        from makerbench import code_cad_arena_runner
        from makerbench.code_cad_objective import RenderArtifacts

        def fake_compiler(scad_path: Path, out_dir: Path) -> RenderArtifacts:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "output.stl").write_text("solid x\nendsolid\n", encoding="utf-8")
            (out_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            return RenderArtifacts(stl_path=out_dir / "output.stl", png_path=out_dir / "preview.png", warnings=("WARNING: w",))

        monkeypatch.setitem(code_cad_arena_runner.SANDBOXED_BACKEND_COMPILERS, "openscad", fake_compiler)
        monkeypatch.setattr(wb, "mesh_objective_gate", lambda spec: (lambda ctx: {"objective_pass_rate": 0.5, "checks": {"renders": True}}))
        store, did, jid, ddir = self._draft(tmp_path)
        assert wb.run_job(ddir, registry) == 0
        job = store.read_draft(did, jid)["job"]
        assert job["status"] == "succeeded" and job["exit_code"] == 0
        objective = json.loads((ddir / "objective.json").read_text(encoding="utf-8"))
        assert objective["render_ok"] is True and objective["objective"]["objective_pass_rate"] == 0.5 and objective["objective"]["declared"] is True
        assert objective["sandbox"] == {"kind": "bwrap", "verified": True, "xvfb": True}
        assert [p["name"] for p in json.loads((ddir / "params.json").read_text())["parameters"]] == ["w_mm"]
        store2, did2, jid2, ddir2 = self._draft(tmp_path, instrument="unlisted")
        assert wb.run_job(ddir2, registry) == 0
        objective2 = json.loads((ddir2 / "objective.json").read_text(encoding="utf-8"))
        assert objective2["objective"]["declared"] is False and "not declared" in objective2["objective"]["note"]

    def test_compile_error_is_a_failed_draft_with_the_message(self, tmp_path, registry, monkeypatch):
        from makerbench import code_cad_arena_runner

        def broken(scad_path, out_dir):
            raise render.CompileError("OpenSCAD exited 1.\nSTDERR:\nERROR: Parser error")

        monkeypatch.setitem(code_cad_arena_runner.SANDBOXED_BACKEND_COMPILERS, "openscad", broken)
        store, did, jid, ddir = self._draft(tmp_path)
        assert wb.run_job(ddir, registry) == 1
        job = store.read_draft(did, jid)["job"]
        assert job["status"] == "failed" and "Parser error" in job["error"]
        objective = json.loads((ddir / "objective.json").read_text(encoding="utf-8"))
        assert objective["render_ok"] is False and objective["failure_stage"] == "compile"

    def test_refuses_a_non_draft_directory(self, tmp_path, registry):
        assert wb.run_job(tmp_path, registry) == 2


class TestCli:
    def test_workbench_job_is_registered_with_its_options(self):
        # Inspect the registered Click command rather than rendered help,
        # which Rich wraps at the terminal width (CI wrapped `--draft-dir`).
        group = typer.main.get_command(arena_app)
        command = group.commands["workbench-job"]
        options = {opt for param in command.params for opt in param.opts}
        assert {"--draft-dir", "--registry"} <= options

    def test_workbench_job_cli_refuses_a_non_draft_directory(self, tmp_path):
        result = CliRunner().invoke(cli_app, ["arena", "workbench-job", "--draft-dir", str(tmp_path)])
        assert result.exit_code == 2 and "not a workbench draft" in " ".join(result.stdout.split())


# --- the real detached job in the real sandbox -------------------------------------


@needs_sandbox
class TestRealJob:
    def _wait(self, service, did, jid, timeout=120.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            draft = service.refresh_draft(did, jid)
            if draft["job"]["status"] in wb.FINISHED_STATUSES:
                return draft
            time.sleep(0.25)
        raise AssertionError("job did not finish")

    def test_master_compiles_detached_in_the_sandbox_and_scores(self, service, tmp_path, monkeypatch):
        monkeypatch.setattr(render, "_run", lambda *a, **k: pytest.fail("host openscad path used by the Studio process"))
        created = service.create_design(origin={"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        assert created["draft"]["job"]["status"] in ("running", "queued")
        draft = self._wait(service, did, jid)
        assert draft["job"]["status"] == "succeeded", draft["job"]
        assert draft["objective"]["sandbox"] == {"kind": "bwrap", "verified": True, "xvfb": True}
        assert draft["objective"]["objective"]["declared"] is True and draft["objective"]["objective"]["objective_pass_rate"] == 1.0
        assert draft["artifacts"] == ["output.stl", "preview.png"]
        stream = "".join(service.stream_log(did, jid, follow=False))
        assert "compiling in the sandbox" in stream and "event: end" in stream and tmp_path.as_posix() not in stream
        r0 = service.save_revision(did, draft_id=jid)
        applied = service.start_params(did, parent_rev_id=r0["rev_id"], values={"w_mm": 50})
        edited = self._wait(service, did, applied["draft_id"])
        assert edited["job"]["status"] == "succeeded" and edited["editor"]["changed"] == {"w_mm": [10, 50]}

    def test_host_include_is_refused_and_cancel_kills_a_running_job(self, service, monkeypatch, tmp_path):
        secret = tmp_path / "host-secret.scad"
        secret.write_text('echo("HOST-SENTINEL");\ncube(40);\n', encoding="utf-8")
        created = service.create_design(origin={"blank": {"backend": "openscad"}})
        did, jid = created["design"]["design_id"], created["draft"]["draft_id"]
        self._wait(service, did, jid)
        rev = service.save_revision(did, draft_id=jid)["rev_id"]
        inc = service.start_edit(did, parent_rev_id=rev, source=f"include <{secret.as_posix()}>\ncube(5);\n")
        draft = self._wait(service, did, inc["draft_id"])
        assert draft["job"]["status"] == "succeeded"
        stl = service.store.artifact_path(did, "drafts", draft["draft_id"], "output.stl").read_text()
        assert "HOST-SENTINEL" not in stl and stl.count("facet normal") == 12
        log = "".join(service.stream_log(did, draft["draft_id"], follow=False))
        assert "Can't open include file" in log and "HOST-SENTINEL" not in log
        monkeypatch.setenv(scad_sandbox.OPENSCAD_TIMEOUT_ENV, "60")
        slow = service.start_edit(did, parent_rev_id=rev, source="union() { for (i = [0:400]) translate([i * 3, 0, 0]) sphere(10, $fn = 120); }\n")
        time.sleep(1.0)
        assert service.refresh_draft(did, slow["draft_id"])["job"]["status"] == "running"
        pid = service.refresh_draft(did, slow["draft_id"])["job"]["pid"]
        assert service.cancel(did, slow["draft_id"])["job"]["status"] == "cancelled"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("job process still alive after cancel")
