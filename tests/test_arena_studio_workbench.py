"""Design workbench API (#788 W3b): the routes over the W3a service.

Hermetic tests replace the detached job process with an inline fake that
writes the same files the real job writes; the service and job body have
their own tests in ``tests/test_workbench_runner.py``. ``TestRealJobs`` runs
the real ``arena workbench-job`` subprocess with the real Bubblewrap sandbox; it is
skipped only when the sandbox is unavailable, and ``MAKERBENCH_REQUIRE_SANDBOX=1``
(CI) turns that skip into a failure.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from makerbench import scad_sandbox
from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio import workbench as wb
from makerbench.redaction import find_host_paths

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_AVAILABLE = scad_sandbox.sandbox_available()
if REQUIRE_SANDBOX and not _AVAILABLE:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start", pytrace=False)
needs_sandbox = pytest.mark.skipif(not _AVAILABLE, reason="OpenSCAD sandbox unavailable here")

CUBE = "w_mm = 10;\ncube(w_mm);\n"
STATIC_APP = Path(__file__).resolve().parents[1] / "makerbench" / "arena_studio" / "static" / "app"


# --- fixtures ------------------------------------------------------------------


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
        ],
        "summary": {"counts": {"scored": 2}},
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    return run_dir


DEAD_PID = 2_147_483_647


class _FakeProc:
    """A never-finishing process handle with a pid that cannot be signalled."""

    pid = DEAD_PID

    def poll(self):
        return None

    def wait(self, timeout=None):
        return None


def _fake_launch(service: wb.WorkbenchService, *, fail: bool = False, hang: bool = False):
    """Replace the detached job with an inline writer of the same files."""

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
            (ddir / "objective.json").write_text(json.dumps({"render_ok": False, "status": "failed", "error": "ERROR: Parser error"}), encoding="utf-8")
            service.store.update_draft_job(design_id, draft_id, status="failed", exit_code=1, finished_at=wb._now(), error="ERROR: Parser error in line 1")
            return
        (art / "output.stl").write_text("solid x\nfacet normal 0 0 0\nendsolid\n", encoding="utf-8")
        (art / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
        (ddir / "objective.json").write_text(json.dumps({
            "render_ok": True, "status": "scored", "artifacts": {"warnings": ["WARNING: fake"]},
            "objective": {"declared": True, "objective_pass_rate": 1.0, "checks": {"renders": {"passed": True}}},
            "sandbox": {"kind": "bwrap", "verified": True, "xvfb": True},
        }), encoding="utf-8")
        service.store.update_draft_job(design_id, draft_id, status="succeeded", exit_code=0, finished_at=wb._now())

    service._launch = launch  # type: ignore[method-assign]
    return launched


@pytest.fixture
def studio(tmp_path: Path, registry: Path, instruments_root: Path, fake_run: Path):
    app = create_studio_app(default_run_dir=fake_run, registry_path=registry, repo_root=tmp_path, instruments_root=instruments_root)
    client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
    client.workbench = _workbench_of(app)  # type: ignore[attr-defined]
    return client


def _workbench_of(app) -> wb.WorkbenchService:
    workbench = app.state.workbench
    assert isinstance(workbench, wb.WorkbenchService)
    return workbench


def _post(client: TestClient, url: str, body: dict, **kw):
    return client.post(url, content=json.dumps(body), headers={"content-type": "application/json"}, **kw)


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file() and p.suffix != ".lock"}


def _assert_no_host_paths(payload, tmp_path: Path) -> None:
    blob = json.dumps(payload)
    assert tmp_path.as_posix() not in blob
    assert not find_host_paths(blob), blob[:400]


# --- origins -------------------------------------------------------------------


class TestOrigins:
    def test_blank_design_compiles_its_origin_draft(self, studio, tmp_path):
        launched = _fake_launch(studio.workbench)
        r = _post(studio, "/api/workbench/designs", {"blank": {"backend": "openscad", "instrument_id": "boxolin"}, "title": "Fresh"})
        assert r.status_code == 202, r.text
        body = r.json()
        did, jid = body["design_id"], body["draft_id"]
        assert launched == [(did, jid)]
        assert body["draft"]["job"]["status"] == "succeeded"
        _assert_no_host_paths(body, tmp_path)
        design = studio.get(f"/api/workbench/designs/{did}").json()
        assert design["title"] == "Fresh" and design["backend"] == "openscad" and design["origin"]["kind"] == "blank"
        assert design["revisions"] == [] and [d["draft_id"] for d in design["drafts"]] == [jid]
        src = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/source")
        assert src.status_code == 200 and "size_mm = 20" in src.text and src.headers["cache-control"] == "no-store"
        assert (tmp_path / "runs" / "workbench" / did / "design.json").is_file()

    def test_master_origin_from_uppercase_cad_dir(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        files = studio.get("/api/workbench/sources/masters", params={"instrument": "boxolin"}).json()
        assert files["files"] == ["boxolin.scad", "private-notes.scad"]  # the symlink and notes.md are not offered
        r = _post(studio, "/api/workbench/designs", {"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}})
        assert r.status_code == 202, r.text
        did, jid = r.json()["design_id"], r.json()["draft_id"]
        assert studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/source").text == CUBE
        design = studio.get(f"/api/workbench/designs/{did}").json()
        assert design["origin"] == {"kind": "master", "instrument_id": "boxolin", "repo_rel_path": "strings/boxolin/CAD/boxolin.scad", "file": "boxolin.scad"}
        assert design["instrument_id"] == "boxolin"
        # the master file itself is untouched
        assert (tmp_path / "instruments" / "strings" / "boxolin" / "CAD" / "boxolin.scad").read_text() == CUBE

    @pytest.mark.parametrize("body, status, needle", [
        ({"master": {"instrument_id": "boxolin", "file": "linked.scad"}}, 404, "unknown master"),
        ({"master": {"instrument_id": "boxolin", "file": "../cad/boxolin.scad"}}, 404, "unknown master"),
        ({"master": {"instrument_id": "boxolin", "file": "notes.md"}}, 400, ".scad or .py"),
        ({"master": {"instrument_id": "boxolin", "file": "nope.scad"}}, 404, "unknown master"),
        ({"master": {"instrument_id": "sneaky", "file": "secret.scad"}}, 404, "unknown instrument"),
        ({"master": {"instrument_id": "hidden", "file": "oracle.scad"}}, 404, "unknown instrument"),
        ({"master": {"instrument_id": "boxolin", "file": "private-notes.scad"}}, 404, "unknown master"),
        ({"master": {"instrument_id": "Nope", "file": "x.scad"}}, 404, "unknown instrument"),
        ({"trial": {"run_id": "round9", "trial_id": "boxolin__seed0__rep0__evil"}}, 404, "not inside its run"),
        ({"trial": {"run_id": "round9", "trial_id": "nope"}}, 404, "unknown trial"),
        ({"trial": {"run_id": "nope", "trial_id": "x"}}, 404, "not found"),
        ({"blank": {"backend": "blender"}}, 400, "backend"),
        ({}, 400, "exactly one"),
        ({"blank": {"backend": "openscad"}, "master": {"instrument_id": "boxolin", "file": "boxolin.scad"}}, 400, "exactly one"),
    ])
    def test_refused_origins_create_nothing(self, studio, tmp_path, body, status, needle):
        launched = _fake_launch(studio.workbench)
        root = tmp_path / "runs" / "workbench"
        before = _snapshot(root) if root.exists() else {}
        r = _post(studio, "/api/workbench/designs", body)
        assert r.status_code == status, r.text
        assert needle in r.json()["detail"]
        assert (_snapshot(root) if root.exists() else {}) == before
        assert launched == []
        assert studio.get("/api/workbench/designs").json()["designs"] == []

    def test_trial_origin_copies_the_candidate_bytes_and_never_writes_the_run(self, studio, tmp_path, fake_run):
        _fake_launch(studio.workbench)
        before = _snapshot(fake_run)
        r = _post(studio, "/api/workbench/designs", {"trial": {"run_id": "round9", "trial_id": "boxolin__seed0__rep0__stub-a"}})
        assert r.status_code == 202, r.text
        did, jid = r.json()["design_id"], r.json()["draft_id"]
        assert studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/source").text == "size = 30;\ncube(size);\n"
        design = studio.get(f"/api/workbench/designs/{did}").json()
        assert design["origin"] == {"kind": "trial", "run_id": "round9", "trial_id": "boxolin__seed0__rep0__stub-a", "model_id": "stub-a", "instrument_id": "boxolin"}
        assert _snapshot(fake_run) == before, "the workbench wrote into an arena run directory"

    def test_masters_unavailable_without_instruments_root(self, tmp_path, registry, fake_run):
        app = create_studio_app(default_run_dir=fake_run, registry_path=registry, repo_root=tmp_path)
        client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        r = client.get("/api/workbench/sources/masters", params={"instrument": "boxolin"})
        assert r.status_code == 400 and "--instruments-root" in r.json()["detail"]
        r = _post(client, "/api/workbench/designs", {"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}})
        assert r.status_code == 400 and "--instruments-root" in r.json()["detail"]


# --- edit, params, save, compare, curate -------------------------------------------


def _origin_revision(studio, **kw) -> tuple[str, str]:
    r = _post(studio, "/api/workbench/designs", {"blank": {"backend": "openscad", "instrument_id": "boxolin"}, **kw})
    assert r.status_code == 202, r.text
    did, jid = r.json()["design_id"], r.json()["draft_id"]
    r = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid, "note": "origin"})
    assert r.status_code == 201, r.text
    return did, r.json()["rev_id"]


class TestEditLoop:
    def test_edit_compile_save_compare(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "source": "size_mm = 40;\ncube(size_mm);\n"})
        assert r.status_code == 202, r.text
        jid = r.json()["draft_id"]
        draft = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}").json()
        assert draft["job"]["status"] == "succeeded" and draft["objective"]["render_ok"] is True
        assert draft["artifacts"] == ["output.stl", "preview.png"]
        png = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/artifacts/preview.png")
        assert png.status_code == 200 and png.content[:4] == b"\x89PNG" and png.headers["content-type"] == "image/png"
        r = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid, "note": "bigger"})
        assert r.status_code == 201, r.text
        r1 = r.json()
        assert r1["seq"] == 2 and r1["parent_rev_id"] == r0 and r1["compile"]["sandbox"] == {"kind": "bwrap", "verified": True, "xvfb": True}
        rev = studio.get(f"/api/workbench/designs/{did}/revisions/{r1['rev_id']}").json()
        assert rev["objective"]["render_ok"] is True and rev["artifacts"] == ["output.stl", "preview.png"]
        assert studio.get(f"/api/workbench/designs/{did}/revisions/{r1['rev_id']}/artifacts/output.stl").status_code == 200
        cmp_ = studio.get(f"/api/workbench/designs/{did}/compare", params={"a": r0, "b": r1["rev_id"]}).json()
        assert any(row["op"] == "+" and "size_mm = 40" in row["text"] for row in cmp_["diff"])
        assert cmp_["parameter_delta"] == {"size_mm": [20, 40]}
        design = studio.get(f"/api/workbench/designs/{did}").json()
        assert [x["seq"] for x in design["revisions"]] == [1, 2]
        _assert_no_host_paths(design, tmp_path)
        _assert_no_host_paths(cmp_, tmp_path)

    def test_parameter_apply_draft_records_changed_values(self, studio):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        params = studio.get(f"/api/workbench/designs/{did}/revisions/{r0}/parameters").json()
        assert [p["name"] for p in params["parameters"]] == ["size_mm"]
        # W5: the registry envelope rides along as context for the tab (plan Q7)
        assert params["envelope_mm"] == [100, 100, 100]
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "params": {"size_mm": 25}})
        assert r.status_code == 202, r.text
        draft = r.json()["draft"]
        assert draft["kind"] == "parameters" and draft["editor"] == {"kind": "parameters", "changed": {"size_mm": [20, 25]}, "voter": "tony"}
        assert studio.get(f"/api/workbench/designs/{did}/drafts/{draft['draft_id']}/source").text == "// New design. Top-level `name = value;` lines become parameters.\nsize_mm = 25;\ncube(size_mm);\n"
        # a no-op apply, an unknown name and a bad value compile nothing
        for body, status in (({"size_mm": 20}, 409), ({"nope": 1}, 400), ({"size_mm": "wide"}, 400)):
            r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "params": body})
            assert r.status_code == status, r.text
        assert len(studio.get(f"/api/workbench/designs/{did}").json()["drafts"]) == 2

    def test_failed_compile_is_a_failed_draft_that_cannot_be_saved(self, studio):
        _fake_launch(studio.workbench, fail=True)
        r = _post(studio, "/api/workbench/designs", {"blank": {"backend": "openscad"}})
        did, jid = r.json()["design_id"], r.json()["draft_id"]
        draft = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}").json()
        assert draft["job"]["status"] == "failed" and "Parser error" in draft["job"]["error"]
        r = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid})
        assert r.status_code == 409 and "only a succeeded" in r.json()["detail"]

    def test_saving_twice_is_409_and_revisions_are_immutable(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        jid = studio.get(f"/api/workbench/designs/{did}").json()["drafts"][0]["draft_id"]
        before = _snapshot(tmp_path / "runs" / "workbench")
        r = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid})
        assert r.status_code == 409 and "already saved" in r.json()["detail"]
        assert _snapshot(tmp_path / "runs" / "workbench") == before
        assert not any(getattr(route, "path", "").startswith("/api/workbench") and "DELETE" in getattr(route, "methods", set()) for route in studio.app.router.routes)
        assert not any(getattr(route, "path", "").startswith("/api/workbench") and ("PUT" in getattr(route, "methods", set()) or "PATCH" in getattr(route, "methods", set())) for route in studio.app.router.routes)

    def test_curation_is_append_only_and_last_wins(self, studio):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        r = _post(studio, f"/api/workbench/designs/{did}/curation", {"rev_id": r0, "pick": True, "title": "Best box", "note": "<img onerror=alert(1)>"})
        assert r.status_code == 201, r.text
        assert r.json()["state"]["pick"] == r0 and r.json()["state"]["title"] == "Best box"
        r = _post(studio, f"/api/workbench/designs/{did}/curation", {"rev_id": r0, "pick": False})
        assert r.json()["state"]["pick"] is None and r.json()["state"]["title"] == "Best box"
        rows = studio.get(f"/api/workbench/designs/{did}").json()["curation"]
        assert rows["note"] == "<img onerror=alert(1)>"  # stored verbatim as text, rendered as text (G9)
        r = _post(studio, f"/api/workbench/designs/{did}/curation", {"rev_id": "r-nope", "pick": True})
        assert r.status_code == 404

    def test_revise_is_not_in_this_slice(self, studio):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "revise": {"entrant": "claude", "feedback": "thicker"}})
        assert r.status_code == 501 and "W6" in r.json()["detail"]
        assert len(studio.get(f"/api/workbench/designs/{did}").json()["drafts"]) == 1


# --- curate and export (W7, G14) --------------------------------------------------


def _repo_snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class TestCurateAndExport:
    def test_curation_history_is_listed_in_order(self, studio):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        _post(studio, f"/api/workbench/designs/{did}/curation", {"title": "First"})
        _post(studio, f"/api/workbench/designs/{did}/curation", {"rev_id": r0, "pick": True, "note": "keep"})
        r = studio.get(f"/api/workbench/designs/{did}/curation")
        assert r.status_code == 200, r.text
        assert r.json()["state"]["pick"] == r0 and r.json()["state"]["title"] == "First" and r.json()["state"]["note"] == "keep"
        assert [(row["title"], row["pick"], row["note"]) for row in r.json()["history"]] == [("First", None, None), (None, True, "keep")]
        assert studio.get("/api/workbench/designs/d-nope-000000/curation").status_code == 404

    def test_export_preview_reads_only_and_export_writes_the_set(self, studio, instruments_root, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        _post(studio, f"/api/workbench/designs/{did}/curation", {"rev_id": r0, "pick": True, "title": "Best box"})
        before = _repo_snapshot(instruments_root)
        r = studio.get(f"/api/workbench/designs/{did}/revisions/{r0}/export")
        assert r.status_code == 200, r.text
        preview = r.json()
        target = f"strings/boxolin/arena/workbench/{did}/{r0}"
        assert preview["target"] == target and preview["exists"] is False
        assert [f["name"] for f in preview["files"]] == ["boxolin-workbench-r1.scad", "boxolin-workbench-r1.stl", "boxolin-workbench-r1.png", "provenance.json", "README.md"]
        assert all(f["path"] == f"{target}/{f['name']}" and f["exists"] is False for f in preview["files"])
        assert _repo_snapshot(instruments_root) == before  # a preview writes nothing
        _assert_no_host_paths(preview, tmp_path)

        r = _post(studio, f"/api/workbench/designs/{did}/revisions/{r0}/export", {})
        assert r.status_code == 201, r.text
        result = r.json()
        assert result["written"] == [f["path"] for f in preview["files"]] and result["replaced"] == []
        _assert_no_host_paths(result, tmp_path)
        after = _repo_snapshot(instruments_root)
        new_files = sorted(set(after) - set(before))
        assert new_files == sorted(result["written"])
        assert all(k in after and after[k] == before[k] for k in before), "an existing repo file changed"
        dest = instruments_root / target
        assert (dest / "boxolin-workbench-r1.scad").read_text() == studio.get(f"/api/workbench/designs/{did}/revisions/{r0}/source").text
        assert (dest / "boxolin-workbench-r1.png").read_bytes()[:4] == b"\x89PNG"
        provenance = json.loads((dest / "provenance.json").read_text())
        assert provenance["schema"] == "makerbench-workbench-export-v1" and provenance["generated"] is True
        assert provenance["design_id"] == did and provenance["rev_id"] == r0 and provenance["seq"] == 1
        assert provenance["curation"] == {"pick": True, "title": "Best box", "note": None}
        assert provenance["compile"]["sandbox"]["kind"] == "bwrap" and provenance["objective"]["render_ok"] is True
        assert tmp_path.as_posix() not in (dest / "provenance.json").read_text()
        readme = (dest / "README.md").read_text()
        assert "NOT a measured master" in readme and "Best box" in readme and "commit it in the instrument repo" in readme
        # the preview now says the target exists
        preview = studio.get(f"/api/workbench/designs/{did}/revisions/{r0}/export").json()
        assert preview["exists"] is True and all(f["exists"] for f in preview["files"])

    def test_export_conflicts_until_replace_is_confirmed(self, studio, instruments_root):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        assert _post(studio, f"/api/workbench/designs/{did}/revisions/{r0}/export", {}).status_code == 201
        dest = instruments_root / "strings" / "boxolin" / "arena" / "workbench" / did / r0
        (dest / "README.md").write_text("edited by hand\n", encoding="utf-8")
        before = _repo_snapshot(instruments_root)
        r = _post(studio, f"/api/workbench/designs/{did}/revisions/{r0}/export", {})
        assert r.status_code == 409 and "Replace" in r.json()["detail"]
        assert _repo_snapshot(instruments_root) == before  # a refused export writes nothing
        r = _post(studio, f"/api/workbench/designs/{did}/revisions/{r0}/export", {"replace": True})
        assert r.status_code == 201, r.text
        assert r.json()["replaced"] == ["README.md", "boxolin-workbench-r1.png", "boxolin-workbench-r1.scad", "boxolin-workbench-r1.stl", "provenance.json"]
        assert "NOT a measured master" in (dest / "README.md").read_text()
        assert sorted(p.name for p in dest.iterdir()) == sorted(r.json()["replaced"])

    def test_export_never_touches_cad_or_runs_git(self, studio, instruments_root, monkeypatch):
        """G14: after an export the instrument repo's `cad/` bytes are unchanged
        and git sees only new files under arena/workbench/. The export itself
        must not spawn anything (git included)."""
        import subprocess

        repo = instruments_root / "strings" / "boxolin"
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        cad_before = _repo_snapshot(repo / "CAD")

        def no_spawn(*args, **kwargs):
            raise AssertionError(f"export spawned a process: {args[:1]}")

        with monkeypatch.context() as m:
            m.setattr(subprocess, "run", no_spawn)
            m.setattr(subprocess, "Popen", no_spawn)
            m.setattr(subprocess, "check_output", no_spawn)
            m.setattr(os, "system", no_spawn)
            r = _post(studio, f"/api/workbench/designs/{did}/revisions/{r0}/export", {})
        assert r.status_code == 201, r.text
        assert _repo_snapshot(repo / "CAD") == cad_before
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, check=True, capture_output=True, text=True).stdout.splitlines()
        assert status and all(line.startswith("?? arena/workbench/") for line in status), status
        assert subprocess.run(["git", "log", "--oneline"], cwd=repo, check=True, capture_output=True, text=True).stdout.count("\n") == 1  # no commit

    def test_export_refuses_uncontained_repo_paths_and_no_instruments_root(self, studio, instruments_root, tmp_path, registry, fake_run):
        _fake_launch(studio.workbench)
        before = _repo_snapshot(tmp_path / "outside") | {"private": json.dumps(sorted(p.as_posix() for p in (instruments_root / "private").rglob("*")))}
        for instrument in ("sneaky", "hidden"):
            did, r0 = _origin_revision(studio, blank={"backend": "openscad", "instrument_id": instrument})
            for method in ("get", "post"):
                r = studio.get(f"/api/workbench/designs/{did}/revisions/{r0}/export") if method == "get" else _post(studio, f"/api/workbench/designs/{did}/revisions/{r0}/export", {})
                assert r.status_code == 404, (instrument, method, r.text)
        assert _repo_snapshot(tmp_path / "outside") | {"private": json.dumps(sorted(p.as_posix() for p in (instruments_root / "private").rglob("*")))} == before
        assert not (instruments_root / "strings" / "boxolin" / "arena").exists()
        # unknown revision and unknown design
        did, r0 = _origin_revision(studio)
        assert studio.get(f"/api/workbench/designs/{did}/revisions/r-nope/export").status_code == 404
        assert _post(studio, f"/api/workbench/designs/{did}/revisions/{r0[:-1]}x/export", {}).status_code == 404
        # a Studio without --instruments-root cannot export at all
        app = create_studio_app(default_run_dir=fake_run, registry_path=registry, repo_root=tmp_path)
        client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        r = client.get(f"/api/workbench/designs/{did}/revisions/{r0}/export")
        assert r.status_code == 400 and "instruments-root" in r.json()["detail"]


# --- guards ---------------------------------------------------------------------


class TestGuards:
    def test_g6_cross_origin_foreign_host_and_wrong_content_type(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        body = {"blank": {"backend": "openscad"}}
        r = studio.post("/api/workbench/designs", content=json.dumps(body), headers={"content-type": "application/json", "origin": "http://evil.example"})
        assert r.status_code == 403
        r = studio.post("/api/workbench/designs", content=json.dumps(body), headers={"content-type": "application/json", "host": "evil.example"})
        assert r.status_code in (400, 403)  # TrustedHost (400) or same-origin (403): refused either way
        r = studio.post("/api/workbench/designs", content=json.dumps(body), headers={"content-type": "text/plain"})
        assert r.status_code == 415
        r = studio.post("/api/workbench/designs", content=json.dumps(body))
        assert r.status_code == 415
        assert not (tmp_path / "runs" / "workbench").exists() or list((tmp_path / "runs" / "workbench").iterdir()) == []

    def test_g7_size_limits_write_nothing(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        root = tmp_path / "runs" / "workbench"
        before = _snapshot(root)
        big_source = "x" * (256 * 1024 + 1)
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "source": big_source})
        assert r.status_code == 413, r.text
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "params": {f"p{i}": i for i in range(501)}})
        assert r.status_code == 413
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "revise": {"feedback": "f" * 9000}})
        assert r.status_code == 413
        jid = studio.get(f"/api/workbench/designs/{did}").json()["drafts"][0]["draft_id"]
        r = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid, "note": "n" * 3000})
        assert r.status_code == 422  # pydantic validator: over 2 KiB
        r = _post(studio, f"/api/workbench/designs/{did}/curation", {"title": "t" * 3000})
        assert r.status_code == 422
        r = _post(studio, "/api/workbench/designs", {"blank": {"backend": "openscad"}, "title": "t" * 3000})
        assert r.status_code == 422
        assert _snapshot(root) == before

    def test_g8_queue_limits_answer_429(self, studio, tmp_path):
        service = studio.workbench
        launched = _fake_launch(service, hang=True)
        service.max_running_per_server = 1
        service.max_queued = 2
        did, r0 = _origin_revision_hanging(studio)
        # one running (the origin), then two queued, then 429
        codes = []
        for i in range(3):
            r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "source": f"cube({i + 2});\n"})
            codes.append(r.status_code)
        assert codes == [202, 202, 429]
        drafts = studio.get(f"/api/workbench/designs/{did}").json()["drafts"]
        statuses = sorted(d["job"]["status"] for d in drafts if d["job"]["status"] != "succeeded")
        assert statuses == ["queued", "queued", "running"], statuses
        assert len(launched) == 2  # the origin draft and the running one; nothing queued was launched
        queued = [d for d in drafts if d["job"]["status"] == "queued"]
        assert sorted(d["queue_position"] for d in queued) == [1, 2]
        # the rejected draft left nothing behind: origin + running + 2 queued
        assert len(list((tmp_path / "runs" / "workbench" / did / "drafts").iterdir())) == 4

    def test_g8_one_running_job_per_design(self, studio):
        service = studio.workbench
        launched = _fake_launch(service, hang=True)
        service.max_running_per_server = 5  # plenty of server capacity: only the per-design rule can queue
        did_a, r_a = _origin_revision_hanging(studio)
        did_b, r_b = _origin_revision_hanging(studio)
        # design a is busy: a second draft for it queues while b's gets a slot
        _post(studio, f"/api/workbench/designs/{did_a}/drafts", {"parent_rev_id": r_a, "source": "cube(3);\n"})
        a_drafts = studio.get(f"/api/workbench/designs/{did_a}").json()["drafts"]
        assert sorted(d["job"]["status"] for d in a_drafts if d["job"]["status"] != "succeeded") == ["queued", "running"]
        assert len(launched) == 4  # both origins and both first drafts; a's second draft was not launched

    def test_cancel_kills_the_job_and_frees_a_slot(self, studio):
        service = studio.workbench
        launched = _fake_launch(service, hang=True)
        service.max_running_per_server = 1
        did, r0 = _origin_revision_hanging(studio)
        running = next(d["draft_id"] for d in studio.get(f"/api/workbench/designs/{did}").json()["drafts"] if d["job"]["status"] == "running")
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "source": "cube(3);\n"})
        queued = r.json()["draft_id"]
        assert r.json()["draft"]["job"]["status"] == "queued"
        r = _post(studio, f"/api/workbench/designs/{did}/drafts/{running}/cancel", {})
        assert r.status_code == 200 and r.json()["job"]["status"] == "cancelled"
        assert studio.get(f"/api/workbench/designs/{did}/drafts/{queued}").json()["job"]["status"] == "running"
        assert len(launched) == 3  # origin, the cancelled one, and the queued one that took its slot
        r = _post(studio, f"/api/workbench/designs/{did}/drafts/{running}/cancel", {})
        assert r.status_code == 409

    def test_g10_ids_and_paths_are_contained(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        jid = studio.get(f"/api/workbench/designs/{did}").json()["drafts"][0]["draft_id"]
        for bad in ("%2E%2E", "a%2Fb", "D-UP", "x.json", "-x", "..x"):
            assert studio.get(f"/api/workbench/designs/{bad}").status_code in (404, 400), bad
            assert studio.get(f"/api/workbench/designs/{did}/revisions/{bad}").status_code in (404, 400), bad
            assert studio.get(f"/api/workbench/designs/{did}/drafts/{bad}").status_code in (404, 400), bad
        for name in ("..%2Fdraft.json", "%2E%2E", "%2e%2e%2fsource.scad", "nope.png", "..x"):
            assert studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/artifacts/{name}").status_code in (404, 400), name
            assert studio.get(f"/api/workbench/designs/{did}/revisions/{r0}/artifacts/{name}").status_code in (404, 400), name
        secret = tmp_path / "secret.txt"
        secret.write_text("s", encoding="utf-8")
        ddir = tmp_path / "runs" / "workbench" / did / "drafts" / jid
        os.symlink(secret, ddir / "artifacts" / "leak.txt")
        assert studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/artifacts/leak.txt").status_code == 404
        os.symlink(tmp_path / "outside-design", tmp_path / "runs" / "workbench" / "d-evil")
        (tmp_path / "outside-design").mkdir()
        (tmp_path / "outside-design" / "design.json").write_text("{}", encoding="utf-8")
        assert studio.get("/api/workbench/designs/d-evil").status_code == 404
        assert all(d["design_id"] != "d-evil" for d in studio.get("/api/workbench/designs").json()["designs"])

    def test_g12_workbench_directories_are_never_runs_and_blind_screens_never_link_it(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, _ = _origin_revision(studio)
        planted = tmp_path / "runs" / "workbench" / did
        (planted / "run_log.json").write_text(json.dumps({"trials": [], "summary": {}}), encoding="utf-8")
        app = create_studio_app(registry_path=tmp_path / "registry.json", repo_root=tmp_path, extra_run_roots=[tmp_path / "runs"])
        client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        runs = client.get("/api/runs").json()["runs"]
        assert all(r.get("run_id") != did for r in runs), runs
        assert not any("workbench" in json.dumps(r) for r in runs)
        for screen in ("vote", "voteStage", "morning", "nightly"):
            for path in STATIC_APP.rglob(f"{screen}*.js"):
                assert "workbench" not in path.read_text(encoding="utf-8").lower(), path

    def test_g12_a_full_workbench_flow_never_writes_under_an_arena_run(self, studio, tmp_path, fake_run):
        _fake_launch(studio.workbench)
        before = _snapshot(tmp_path / "runs" / "code_cad_arena")
        r = _post(studio, "/api/workbench/designs", {"trial": {"run_id": "round9", "trial_id": "boxolin__seed0__rep0__stub-a"}})
        did, jid = r.json()["design_id"], r.json()["draft_id"]
        r0 = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid}).json()["rev_id"]
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0, "source": "cube(2);\n"})
        _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": r.json()["draft_id"]})
        _post(studio, f"/api/workbench/designs/{did}/curation", {"rev_id": r0, "pick": True})
        assert _snapshot(tmp_path / "runs" / "code_cad_arena") == before

    def test_g15_no_host_path_in_any_body_or_sse_line(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        jid = studio.get(f"/api/workbench/designs/{did}").json()["drafts"][0]["draft_id"]
        # plant a host path in the log, as a compile error would
        (tmp_path / "runs" / "workbench" / did / "drafts" / jid / "job.log").write_text(f"ERROR: cannot open {tmp_path}/secret.scad\n", encoding="utf-8")
        bodies = [
            studio.get("/api/workbench/designs").json(),
            studio.get(f"/api/workbench/designs/{did}").json(),
            studio.get(f"/api/workbench/designs/{did}/revisions/{r0}").json(),
            studio.get(f"/api/workbench/designs/{did}/drafts/{jid}").json(),
            studio.get(f"/api/workbench/designs/{did}/compare", params={"a": r0, "b": r0}).json(),
            studio.get("/api/workbench/sources/masters", params={"instrument": "boxolin"}).json(),
        ]
        for body in bodies:
            _assert_no_host_paths(body, tmp_path)
        r = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/log/stream", params={"follow": "false"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert tmp_path.as_posix() not in r.text and "cannot open" in r.text
        assert not find_host_paths(r.text), r.text
        r = studio.get(f"/api/workbench/designs/{did}/revisions/nope")
        assert tmp_path.as_posix() not in r.text

    def test_read_only_routes_write_nothing(self, studio, tmp_path):
        _fake_launch(studio.workbench)
        did, r0 = _origin_revision(studio)
        jid = studio.get(f"/api/workbench/designs/{did}").json()["drafts"][0]["draft_id"]
        root = tmp_path / "runs" / "workbench"
        before = _snapshot(root)
        for url, params in (
            ("/api/workbench/designs", None), (f"/api/workbench/designs/{did}", None),
            (f"/api/workbench/designs/{did}/revisions/{r0}", None), (f"/api/workbench/designs/{did}/revisions/{r0}/source", None),
            (f"/api/workbench/designs/{did}/revisions/{r0}/parameters", None),
            (f"/api/workbench/designs/{did}/revisions/{r0}/artifacts/preview.png", None),
            (f"/api/workbench/designs/{did}/compare", {"a": r0, "b": r0}), (f"/api/workbench/designs/{did}/drafts/{jid}", None),
            (f"/api/workbench/designs/{did}/drafts/{jid}/source", None), (f"/api/workbench/designs/{did}/drafts/{jid}/log/stream", {"follow": "false"}),
            (f"/api/workbench/designs/{did}/drafts/{jid}/artifacts/output.stl", None), ("/api/workbench/sources/masters", {"instrument": "boxolin"}),
        ):
            assert studio.get(url, params=params).status_code == 200, url
        assert _snapshot(root) == before


def _origin_revision_hanging(studio) -> tuple[str, str]:
    """With a hanging fake job, the origin draft never finishes; give the
    design a saved origin revision by finishing that draft by hand."""

    service = studio.workbench
    r = _post(studio, "/api/workbench/designs", {"blank": {"backend": "openscad", "instrument_id": "boxolin"}})
    assert r.status_code == 202, r.text
    did, jid = r.json()["design_id"], r.json()["draft_id"]
    ddir = service.store.draft_dir(did, jid)
    (ddir / "artifacts").mkdir(exist_ok=True)
    (ddir / "artifacts" / "output.stl").write_text("solid x\nendsolid\n", encoding="utf-8")
    service.store.update_draft_job(did, jid, status="succeeded", exit_code=0, finished_at=wb._now())
    r = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid})
    assert r.status_code == 201, r.text
    rev = r.json()["rev_id"]
    # now make a fresh draft the "running" one so the slot is taken
    r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": rev, "source": "cube(1);\n"})
    assert r.status_code == 202 and r.json()["draft"]["job"]["status"] == "running", r.text
    return did, rev


# --- CLI ------------------------------------------------------------------------------


class TestCli:
    def test_studio_option_and_job_command_are_registered(self):
        # Inspect the registered Click commands rather than rendered help,
        # which Rich wraps at the terminal width (CI wrapped the option name).
        import typer.main

        from makerbench.cli_arena import arena_app

        group = typer.main.get_command(arena_app)
        studio_opts = {opt for param in group.commands["studio"].params for opt in param.opts}
        assert "--instruments-root" in studio_opts
        assert "workbench-job" in group.commands

    def test_create_studio_app_threads_the_instruments_root(self, tmp_path, registry, instruments_root):
        app = create_studio_app(registry_path=registry, repo_root=tmp_path, instruments_root=instruments_root)
        assert _workbench_of(app).instruments_root == instruments_root.resolve()


# --- the real job process, real sandbox -------------------------------------------


@needs_sandbox
class TestRealJobs:
    def _wait(self, client: TestClient, did: str, jid: str, timeout: float = 120.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            draft = client.get(f"/api/workbench/designs/{did}/drafts/{jid}").json()
            if draft["job"]["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
                return draft
            time.sleep(0.25)
        raise AssertionError("job did not finish")

    def test_origin_compile_runs_detached_in_the_sandbox_and_scores(self, studio, tmp_path, monkeypatch):
        from makerbench import render

        monkeypatch.setattr(render, "_run", lambda *a, **k: pytest.fail("host openscad path used by the Studio process"))
        r = _post(studio, "/api/workbench/designs", {"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}, "title": "Real"})
        assert r.status_code == 202, r.text
        did, jid = r.json()["design_id"], r.json()["draft_id"]
        assert r.json()["draft"]["job"]["status"] in ("running", "queued")
        draft = self._wait(studio, did, jid)
        assert draft["job"]["status"] == "succeeded", draft["job"]
        assert draft["objective"]["render_ok"] is True
        assert draft["objective"]["sandbox"] == {"kind": "bwrap", "verified": True, "xvfb": True}
        assert draft["objective"]["objective"]["declared"] is True
        assert draft["objective"]["objective"]["objective_pass_rate"] == 1.0
        assert draft["artifacts"] == ["output.stl", "preview.png"]
        png = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/artifacts/preview.png")
        assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
        stream = studio.get(f"/api/workbench/designs/{did}/drafts/{jid}/log/stream", params={"follow": "false"}).text
        assert "compiling in the sandbox" in stream and "succeeded" in stream and "event: end" in stream
        assert tmp_path.as_posix() not in stream
        params = json.loads((tmp_path / "runs" / "workbench" / did / "drafts" / jid / "params.json").read_text())
        assert [p["name"] for p in params["parameters"]] == ["w_mm"]
        # save it, then edit -> compile -> compare, all through the real job
        r0 = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid, "note": "origin"}).json()
        assert r0["compile"]["sandbox"]["kind"] == "bwrap"
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": r0["rev_id"], "params": {"w_mm": 50}})
        assert r.status_code == 202, r.text
        edited = self._wait(studio, did, r.json()["draft_id"])
        assert edited["job"]["status"] == "succeeded" and edited["editor"]["changed"] == {"w_mm": [10, 50]}
        r1 = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": edited["draft_id"]}).json()
        cmp_ = studio.get(f"/api/workbench/designs/{did}/compare", params={"a": r0["rev_id"], "b": r1["rev_id"]}).json()
        assert cmp_["parameter_delta"] == {"w_mm": [10, 50]}

    def test_syntax_error_is_a_failed_draft_with_the_message(self, studio):
        r = _post(studio, "/api/workbench/designs", {"blank": {"backend": "openscad"}})
        did, jid = r.json()["design_id"], r.json()["draft_id"]
        self._wait(studio, did, jid)
        rev = _post(studio, f"/api/workbench/designs/{did}/revisions", {"draft_id": jid}).json()["rev_id"]
        r = _post(studio, f"/api/workbench/designs/{did}/drafts", {"parent_rev_id": rev, "source": "cube(5;\n"})
        draft = self._wait(studio, did, r.json()["draft_id"])
        assert draft["job"]["status"] == "failed" and "OpenSCAD exited" in draft["job"]["error"]
        assert draft["objective"]["render_ok"] is False and draft["objective"]["failure_stage"] == "compile"
