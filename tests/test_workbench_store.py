"""Append-only workbench revision store (#788 W2): containment, immutability,
content-addressed ids, concurrency, curation and retention."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from makerbench import workbench_store as ws
from makerbench.workbench_store import (
    Conflict,
    NotFound,
    TooLarge,
    WorkbenchError,
    WorkbenchStore,
    source_diff,
)

HUMAN = {"kind": "human", "voter": "tony"}
ORIGIN = {"kind": "master", "instrument_id": "ukulele", "repo_rel_path": "strings/ukulele", "file": "ukulele.scad"}


@pytest.fixture
def store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / "runs" / "workbench")


def _design(store: WorkbenchStore, **kw) -> dict:
    return store.create_design(instrument_id="ukulele", backend="openscad", title="Uke", origin=ORIGIN, **kw)


def _finished_draft(store, design_id, *, parent=None, source="cube(1);\n", status="succeeded", editor=HUMAN, objective=None):
    draft = store.create_draft(design_id, parent_rev_id=parent, source=source, editor=editor)
    ddir = store.draft_dir(design_id, draft["draft_id"])
    if objective is not None:
        (ddir / "objective.json").write_text(json.dumps(objective), encoding="utf-8")
    (ddir / "artifacts").mkdir()
    (ddir / "artifacts" / "output.stl").write_text("solid x\nendsolid\n", encoding="utf-8")
    (ddir / "job.log").write_text("compiled\n", encoding="utf-8")
    store.update_draft_job(design_id, draft["draft_id"], status=status, exit_code=0 if status == "succeeded" else 1)
    return draft


def draft_rev_id(rows: list[dict]) -> str:
    """The rev_id of the second row (the one whose rollback failed)."""

    return rows[1]["rev_id"]


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file's mtime and bytes, except the zero-byte ``*.lock`` files
    that ``run_log_io.file_lock`` creates on first use (not store content)."""

    return {
        p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and p.suffix != ".lock"
    }


# --- ids and containment -----------------------------------------------------


class TestContainment:
    @pytest.mark.parametrize("bad", ["..", "a/b", "A", "x.y", "", "-x", "a" * 65, "d é", "..x"])
    def test_id_rule(self, bad):
        assert not ws.is_valid_id(bad)

    def test_id_rule_accepts_workbench_ids(self):
        assert ws.is_valid_id("d-ukulele-a1b2c3") and ws.is_valid_id("r-0123456789abcdef") and ws.is_valid_id("j-abc")

    def test_unknown_ids_are_not_found_and_read_nothing(self, store):
        for bad in ("..", "../..", "a/b", "%2e%2e", "D", "x.json"):
            with pytest.raises(NotFound):
                store.read_design(bad)
            with pytest.raises(NotFound):
                store.contained(bad)

    def test_parts_cannot_climb(self, store):
        design = _design(store)
        for part in ("..", "../x", "a/b", "a\\b", ".", ""):
            with pytest.raises(NotFound):
                store.contained(design["design_id"], part)

    def test_symlinked_design_dir_is_refused(self, store, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "design.json").write_text(json.dumps({"design_id": "d-evil"}), encoding="utf-8")
        store.root.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, store.root / "d-evil")
        with pytest.raises(NotFound):
            store.read_design("d-evil")
        assert all(row["design_id"] != "d-evil" for row in store.list_designs())

    def test_symlinked_artifact_is_refused(self, store, tmp_path):
        design = _design(store)
        draft = _finished_draft(store, design["design_id"])
        secret = tmp_path / "secret.txt"
        secret.write_text("s", encoding="utf-8")
        ddir = store.draft_dir(design["design_id"], draft["draft_id"])
        os.symlink(secret, ddir / "artifacts" / "leak.txt")
        with pytest.raises(NotFound):
            store.artifact_path(design["design_id"], "drafts", draft["draft_id"], "leak.txt")
        for name in ("../draft.json", "..", "a/b", ""):
            with pytest.raises(NotFound):
                store.artifact_path(design["design_id"], "drafts", draft["draft_id"], name)
        assert store.artifact_path(design["design_id"], "drafts", draft["draft_id"], "output.stl").name == "output.stl"

    def test_symlinked_draft_dir_is_not_listed_read_expired_or_saved(self, store, tmp_path):
        design = _design(store)
        did = design["design_id"]
        real = _finished_draft(store, did)
        outside = tmp_path / "outside-draft"
        outside.mkdir()
        leaked = {"schema": ws.DRAFT_SCHEMA, "draft_id": "j-leak", "design_id": did, "backend": "openscad",
                  "kind": "edit", "parent_rev_id": None, "source_name": "source.scad",
                  "source_sha256": "0" * 64, "editor": HUMAN, "created_at": "2000-01-01T00:00:00+00:00",
                  "job": {"status": "succeeded", "pid": None, "started_at": None, "finished_at": None, "exit_code": 0, "error": None}}
        (outside / "draft.json").write_text(json.dumps(leaked), encoding="utf-8")
        (outside / "source.scad").write_text("cube(9);\n", encoding="utf-8")
        os.symlink(outside, store.root / did / "drafts" / "j-leak")
        # a symlink to another draft *inside* the root is refused too
        os.symlink(store.root / did / "drafts" / real["draft_id"], store.root / did / "drafts" / "j-alias")
        listed = [d["draft_id"] for d in store.list_drafts(did)]
        assert listed == [real["draft_id"]]
        assert [d["draft_id"] for d in store.get_design(did)["drafts"]] == [real["draft_id"]]
        for alias in ("j-leak", "j-alias"):
            with pytest.raises(NotFound):
                store.read_draft(did, alias)
            with pytest.raises(NotFound):
                store.draft_source(did, alias)
            with pytest.raises(NotFound):
                store.save_revision(did, draft_id=alias)
        # retention never follows the link: the outside files survive
        removed = store.expire_drafts(ttl_s=0, now=time.time() + 10)
        assert removed == [f"{did}/{real['draft_id']}"]
        assert (outside / "draft.json").is_file() and (outside / "source.scad").is_file()

    def test_symlinked_artifacts_directory_is_ignored(self, store, tmp_path):
        design = _design(store)
        did = design["design_id"]
        draft = store.create_draft(did, parent_rev_id=None, source="cube(1);\n", editor=HUMAN)
        ddir = store.draft_dir(did, draft["draft_id"])
        outside = tmp_path / "outside-artifacts"
        outside.mkdir()
        (outside / "secret.stl").write_text("s", encoding="utf-8")
        os.symlink(outside, ddir / "artifacts")
        store.update_draft_job(did, draft["draft_id"], status="succeeded", exit_code=0)
        assert store.read_draft(did, draft["draft_id"])["artifacts"] == []
        with pytest.raises(NotFound):
            store.artifact_path(did, "drafts", draft["draft_id"], "secret.stl")
        rev = store.save_revision(did, draft_id=draft["draft_id"])
        assert store.read_revision(did, rev["rev_id"])["artifacts"] == []
        assert not (store.revision_dir(did, rev["rev_id"]) / "artifacts").exists()

    def test_symlinked_revision_dir_is_refused(self, store, tmp_path):
        design = _design(store)
        did = design["design_id"]
        rev = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        os.symlink(store.revision_dir(did, rev["rev_id"]), store.root / did / "revisions" / "r-alias")
        with pytest.raises(NotFound):
            store.read_revision(did, "r-alias")
        with pytest.raises(NotFound):
            store.create_draft(did, parent_rev_id="r-alias", source="cube(2);\n", editor=HUMAN)

    def test_dicts_carry_no_host_paths(self, store, tmp_path):
        design = _design(store)
        draft = _finished_draft(store, design["design_id"])
        rev = store.save_revision(design["design_id"], draft_id=draft["draft_id"])
        blob = json.dumps([store.get_design(design["design_id"]), store.read_revision(design["design_id"], rev["rev_id"]), store.list_designs()])
        assert tmp_path.as_posix() not in blob


# --- designs and drafts --------------------------------------------------------


class TestDesignsAndDrafts:
    def test_create_and_list(self, store):
        design = _design(store)
        assert design["design_id"].startswith("d-ukulele-")
        assert (store.root / design["design_id"] / "design.json").is_file()
        rows = store.list_designs()
        assert [r["design_id"] for r in rows] == [design["design_id"]]
        assert rows[0]["revision_count"] == 0 and rows[0]["pick"] is None

    def test_validation(self, store):
        with pytest.raises(WorkbenchError, match="backend"):
            store.create_design(instrument_id="x", backend="blender", title="t", origin=ORIGIN)
        with pytest.raises(WorkbenchError, match="instrument_id"):
            store.create_design(instrument_id="../x", backend="openscad", title="t", origin=ORIGIN)
        with pytest.raises(WorkbenchError, match="origin.kind"):
            store.create_design(instrument_id="x", backend="openscad", title="t", origin={"kind": "url"})
        with pytest.raises(TooLarge):
            store.create_design(instrument_id="x", backend="openscad", title="t" * 3000, origin=ORIGIN)

    def test_draft_writes_source_by_backend(self, store):
        design = _design(store)
        draft = store.create_draft(design["design_id"], parent_rev_id=None, source="cube(2);\n", editor=HUMAN)
        assert draft["source_name"] == "source.scad" and draft["job"]["status"] == "queued"
        assert store.draft_source(design["design_id"], draft["draft_id"]) == "cube(2);\n"
        cq = store.create_design(instrument_id="drum", backend="cadquery", title="d", origin={"kind": "blank"})
        d2 = store.create_draft(cq["design_id"], parent_rev_id=None, source="X = 1\n", editor=HUMAN)
        assert d2["source_name"] == "source.py"

    def test_source_limits(self, store):
        design = _design(store)
        with pytest.raises(TooLarge):
            store.create_draft(design["design_id"], parent_rev_id=None, source="x" * (ws.MAX_SOURCE_BYTES + 1), editor=HUMAN)
        with pytest.raises(WorkbenchError, match="NUL"):
            store.create_draft(design["design_id"], parent_rev_id=None, source="a\x00b", editor=HUMAN)
        assert store.list_drafts(design["design_id"]) == []

    def test_editor_validation(self, store):
        design = _design(store)
        with pytest.raises(WorkbenchError, match="editor.kind"):
            store.create_draft(design["design_id"], parent_rev_id=None, source="x", editor={"kind": "robot"})
        with pytest.raises(WorkbenchError, match="confinement"):
            store.create_draft(design["design_id"], parent_rev_id=None, source="x", editor={"kind": "model", "model_id": "m", "provider": "p", "confinement": "maybe"})
        model = {"kind": "model", "model_id": "claude-x", "provider": "claude", "confinement": "restricted-tools", "prompt": "thicken the walls", "max_turns": 40}
        draft = store.create_draft(design["design_id"], parent_rev_id=None, source="x", editor=model)
        assert draft["editor"]["prompt_sha256"] and "prompt" not in draft["editor"]
        params = {"kind": "parameters", "changed": {"wall_mm": (3, 4)}}
        d2 = store.create_draft(design["design_id"], parent_rev_id=None, source="y", editor=params)
        assert d2["editor"]["changed"] == {"wall_mm": [3, 4]}

    def test_parent_must_exist_and_is_required_once_revisions_exist(self, store):
        design = _design(store)
        with pytest.raises(NotFound):
            store.create_draft(design["design_id"], parent_rev_id="r-nope", source="x", editor=HUMAN)
        draft = _finished_draft(store, design["design_id"])
        store.save_revision(design["design_id"], draft_id=draft["draft_id"])
        with pytest.raises(Conflict, match="parent_rev_id"):
            store.create_draft(design["design_id"], parent_rev_id=None, source="x", editor=HUMAN)

    def test_update_job_validates_and_locks(self, store):
        design = _design(store)
        draft = store.create_draft(design["design_id"], parent_rev_id=None, source="x", editor=HUMAN)
        out = store.update_draft_job(design["design_id"], draft["draft_id"], status="running", pid=123)
        assert out["job"]["status"] == "running" and out["job"]["pid"] == 123
        with pytest.raises(WorkbenchError, match="status"):
            store.update_draft_job(design["design_id"], draft["draft_id"], status="done")
        with pytest.raises(WorkbenchError, match="unknown job field"):
            store.update_draft_job(design["design_id"], draft["draft_id"], owner="me")


# --- revisions ------------------------------------------------------------------


class TestValidateBeforeMutate:
    """Sol's review: an invalid input must not leave orphan directories."""

    @pytest.mark.parametrize(
        "kwargs, exc",
        [
            ({"editor": {"kind": "bad"}}, WorkbenchError),
            ({"editor": {"kind": "model", "model_id": "m", "provider": "p", "confinement": "verified", "prompt": "p" * 9000}}, TooLarge),
            ({"editor": {"kind": "model", "model_id": "m", "provider": "p", "confinement": "nope"}}, WorkbenchError),
            ({"editor": "not-an-object"}, WorkbenchError),
            ({"kind": "bogus"}, WorkbenchError),
            ({"source": "x" * (ws.MAX_SOURCE_BYTES + 1)}, TooLarge),
            ({"source": "cube(1);\x00"}, WorkbenchError),
            ({"source": 42}, WorkbenchError),
            ({"parent_rev_id": "r-doesnotexist"}, NotFound),
            ({"parent_rev_id": "../x"}, NotFound),
        ],
    )
    def test_rejected_draft_writes_nothing(self, store, kwargs, exc):
        design = _design(store)
        did = design["design_id"]
        before = _snapshot(store.root)
        args = {"parent_rev_id": None, "source": "cube(1);\n", "editor": HUMAN, **kwargs}
        with pytest.raises(exc):
            store.create_draft(did, **args)
        assert _snapshot(store.root) == before
        assert list((store.root / did / "drafts").iterdir()) == []

    def test_draft_without_parent_once_revisions_exist_writes_nothing(self, store):
        design = _design(store)
        did = design["design_id"]
        store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        before = _snapshot(store.root)
        with pytest.raises(Conflict):
            store.create_draft(did, parent_rev_id=None, source="cube(2);\n", editor=HUMAN)
        assert _snapshot(store.root) == before

    @pytest.mark.parametrize(
        "kwargs, exc",
        [
            ({"origin": {"kind": "nope"}}, WorkbenchError),
            ({"origin": "text"}, WorkbenchError),
            ({"origin": {"kind": "master", "file": "f" * 3000}}, TooLarge),
            ({"instrument_id": "Bad Id"}, WorkbenchError),
            ({"backend": "blender"}, WorkbenchError),
            ({"title": "t" * 3000}, TooLarge),
            ({"title": 7}, WorkbenchError),
        ],
    )
    def test_rejected_design_writes_nothing(self, store, kwargs, exc):
        _design(store)  # the root exists and holds one design
        before = _snapshot(store.root)
        names = sorted(p.name for p in store.root.iterdir())
        args = {"instrument_id": "ukulele", "backend": "openscad", "title": "Uke", "origin": ORIGIN, **kwargs}
        with pytest.raises(exc):
            store.create_design(**args)
        assert _snapshot(store.root) == before
        assert sorted(p.name for p in store.root.iterdir()) == names

    def test_longest_instrument_id_keeps_the_random_suffix(self, store):
        instrument = "a" * 64
        assert ws.is_valid_id(instrument)
        ids = set()
        for _ in range(5):
            design = store.create_design(instrument_id=instrument, backend="openscad", title="", origin=ORIGIN)
            ids.add(design["design_id"])
            assert ws.is_valid_id(design["design_id"]) and len(design["design_id"]) <= 64
            assert design["design_id"].startswith("d-" + "a" * 55 + "-")
            assert len(design["design_id"].rsplit("-", 1)[1]) == 6
        assert len(ids) == 5
        assert len(store.list_designs()) == 5

    def test_design_id_collision_is_retried(self, store, monkeypatch):
        first = _design(store)
        calls = iter([first["design_id"], "d-ukulele-fresh1"])
        monkeypatch.setattr(WorkbenchStore, "new_design_id", staticmethod(lambda instrument_id: next(calls)))
        second = _design(store)
        assert second["design_id"] == "d-ukulele-fresh1"
        assert len(store.list_designs()) == 2


class TestRevisions:
    def test_save_is_content_addressed_and_immutable(self, store):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did, objective={"render_ok": True, "artifacts": {"warnings": ["w1"]}, "sandbox": {"kind": "bwrap"}})
        rev = store.save_revision(did, draft_id=draft["draft_id"], note="origin")
        assert rev["rev_id"].startswith("r-") and len(rev["rev_id"]) == 18
        assert rev["seq"] == 1 and rev["parent_rev_id"] is None and rev["origin"] == design["origin"]
        assert rev["compile"] == {"status": "succeeded", "render_ok": True, "warnings": ["w1"], "sandbox": {"kind": "bwrap"}}
        rdir = store.revision_dir(did, rev["rev_id"])
        assert sorted(p.name for p in rdir.iterdir()) == ["artifacts", "job.log", "objective.json", "revision.json", "source.scad"]
        assert store.revision_source(did, rev["rev_id"]) == "cube(1);\n"
        assert store.read_revision(did, rev["rev_id"])["artifacts"] == ["output.stl"]
        # deterministic id
        assert rev["rev_id"] == WorkbenchStore.revision_id(design_id=did, parent_rev_id=None, source_sha256=draft["source_sha256"], editor=draft["editor"])

        # saving the identical draft again is a conflict that changes no bytes
        before = _snapshot(store.root)
        with pytest.raises(Conflict, match="already saved"):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before
        assert len(store.list_revisions(did)) == 1

    def test_same_bytes_different_editor_kind_is_a_new_revision(self, store):
        design = _design(store)
        did = design["design_id"]
        a = _finished_draft(store, did)
        r1 = store.save_revision(did, draft_id=a["draft_id"])
        b = _finished_draft(store, did, parent=r1["rev_id"], editor={"kind": "parameters", "changed": {}})
        r2 = store.save_revision(did, draft_id=b["draft_id"])
        assert r2["rev_id"] != r1["rev_id"] and r2["seq"] == 2 and r2["parent_rev_id"] == r1["rev_id"]

    def test_siblings_from_one_parent_both_land(self, store):
        design = _design(store)
        did = design["design_id"]
        r0 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        a = _finished_draft(store, did, parent=r0["rev_id"], source="cube(2);\n")
        b = _finished_draft(store, did, parent=r0["rev_id"], source="cube(3);\n")
        ra = store.save_revision(did, draft_id=a["draft_id"])
        rb = store.save_revision(did, draft_id=b["draft_id"])
        rows = store.list_revisions(did)
        assert [r["seq"] for r in rows] == [1, 2, 3]
        assert {ra["parent_rev_id"], rb["parent_rev_id"]} == {r0["rev_id"]}
        assert ra["rev_id"] != rb["rev_id"]

    def test_unfinished_or_failed_drafts(self, store):
        design = _design(store)
        did = design["design_id"]
        running = store.create_draft(did, parent_rev_id=None, source="x", editor=HUMAN)
        with pytest.raises(Conflict, match="queued"):
            store.save_revision(did, draft_id=running["draft_id"])
        failed = _finished_draft(store, did, status="failed")
        with pytest.raises(Conflict, match="only a succeeded"):
            store.save_revision(did, draft_id=failed["draft_id"])
        rev = store.save_revision(did, draft_id=failed["draft_id"], allow_failed=True)
        assert rev["compile"]["status"] == "failed"

    def test_note_limit(self, store):
        design = _design(store)
        draft = _finished_draft(store, design["design_id"])
        with pytest.raises(TooLarge):
            store.save_revision(design["design_id"], draft_id=draft["draft_id"], note="n" * 3000)

    def test_twenty_concurrent_savers_keep_every_index_line(self, store):
        design = _design(store)
        did = design["design_id"]
        r0 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        drafts = [_finished_draft(store, did, parent=r0["rev_id"], source=f"cube({i + 2});\n") for i in range(20)]
        errors: list[BaseException] = []
        start = threading.Barrier(20)

        def worker(draft_id: str) -> None:
            try:
                start.wait(5)
                store.save_revision(did, draft_id=draft_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(d["draft_id"],)) for d in drafts]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert errors == []
        rows = store.list_revisions(did)
        assert sorted(r["seq"] for r in rows) == list(range(1, 22))
        assert len({r["rev_id"] for r in rows}) == 21
        assert len((store.root / did / "index.jsonl").read_text().splitlines()) == 21

    def test_tampered_draft_source_is_refused_and_writes_nothing(self, store):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did, source="cube(1);\n")
        ddir = store.draft_dir(did, draft["draft_id"])
        (ddir / "source.scad").write_text("cube(999);\n", encoding="utf-8")
        before = _snapshot(store.root)
        with pytest.raises(Conflict, match="source changed"):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before
        assert store.list_revisions(did) == []
        assert list((store.root / did / "revisions").iterdir()) == []

    def test_revision_hash_matches_the_stored_bytes(self, store):
        design = _design(store)
        did = design["design_id"]
        rev = store.save_revision(did, draft_id=_finished_draft(store, did, source="cube(7);\n")["draft_id"])
        stored = (store.revision_dir(did, rev["rev_id"]) / "source.scad").read_bytes()
        assert rev["source_sha256"] == hashlib.sha256(stored).hexdigest()
        assert store.list_revisions(did)[0]["source_sha256"] == rev["source_sha256"]

    def test_tampered_draft_editor_is_refused(self, store):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did)
        path = store.draft_dir(did, draft["draft_id"]) / "draft.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["editor"] = {"kind": "model", "confinement": "totally"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = _snapshot(store.root)
        with pytest.raises(WorkbenchError):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before

    @pytest.mark.parametrize("failing", ["_copy_artifact_tree", "copyfile", "atomic_write_json"])
    def test_injected_failure_leaves_nothing_and_retry_succeeds(self, store, monkeypatch, failing):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did, objective={"render_ok": True})
        before = _snapshot(store.root)
        holder = ws.shutil if failing == "copyfile" else ws
        real = getattr(holder, failing)
        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(28, "No space left on device")
            return real(*args, **kwargs)

        monkeypatch.setattr(holder, failing, boom)
        with pytest.raises(OSError):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before, "a failed save left bytes behind"
        assert list((store.root / did / "revisions").iterdir()) == []
        assert store.list_revisions(did) == []
        rev = store.save_revision(did, draft_id=draft["draft_id"])
        assert store.list_revisions(did)[0]["rev_id"] == rev["rev_id"]
        assert store.revision_source(did, rev["rev_id"]) == "cube(1);\n"
        assert store.read_revision(did, rev["rev_id"])["artifacts"] == ["output.stl"]

    def test_failed_publish_rename_leaves_nothing(self, store, monkeypatch):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did)
        before = _snapshot(store.root)
        real_rename = Path.rename

        def boom(self, target):
            if ".staging-" in self.name:
                raise OSError(5, "Input/output error")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", boom)
        with pytest.raises(WorkbenchError, match="could not publish"):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before
        monkeypatch.setattr(Path, "rename", real_rename)
        rev = store.save_revision(did, draft_id=draft["draft_id"])
        assert store.list_revisions(did)[0]["rev_id"] == rev["rev_id"]

    @pytest.mark.parametrize("where", ["file", "nested_dir", "nested_file"])
    def test_symlink_anywhere_in_the_artifact_tree_refuses_the_save(self, store, tmp_path, where):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did)
        ddir = store.draft_dir(did, draft["draft_id"])
        secret = tmp_path / "host-secret.txt"
        secret.write_text("HOST-BYTES", encoding="utf-8")
        outside_dir = tmp_path / "host-dir"
        outside_dir.mkdir()
        (outside_dir / "inner.stl").write_text("HOST-BYTES", encoding="utf-8")
        if where == "file":
            os.symlink(secret, ddir / "artifacts" / "leak.txt")
        elif where == "nested_dir":
            (ddir / "artifacts" / "views").mkdir()
            os.symlink(outside_dir, ddir / "artifacts" / "views" / "link")
        else:
            (ddir / "artifacts" / "views").mkdir()
            os.symlink(secret, ddir / "artifacts" / "views" / "leak.txt")
        before = _snapshot(store.root)
        with pytest.raises(WorkbenchError, match="symlink"):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before
        assert list((store.root / did / "revisions").iterdir()) == []
        assert store.list_revisions(did) == []
        # no store-owned file carries the host bytes (the draft's own link is skipped)
        assert b"HOST-BYTES" not in b"".join(
            p.read_bytes() for p in store.root.rglob("*") if p.is_file() and not p.is_symlink()
        )

    def test_nested_regular_artifact_dirs_are_copied(self, store):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did)
        ddir = store.draft_dir(did, draft["draft_id"])
        (ddir / "artifacts" / "views").mkdir()
        (ddir / "artifacts" / "views" / "iso.png").write_bytes(b"\x89PNG")
        rev = store.save_revision(did, draft_id=draft["draft_id"])
        rdir = store.revision_dir(did, rev["rev_id"])
        assert (rdir / "artifacts" / "views" / "iso.png").read_bytes() == b"\x89PNG"
        assert (rdir / "artifacts" / "output.stl").is_file()

    def test_index_append_failure_unpublishes_and_retry_succeeds(self, store, monkeypatch):
        design = _design(store)
        did = design["design_id"]
        draft = _finished_draft(store, did)
        before = _snapshot(store.root)
        real = WorkbenchStore._append_index_row
        calls = {"n": 0}

        def boom(self, index_path, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(28, "No space left on device")
            return real(self, index_path, payload)

        monkeypatch.setattr(WorkbenchStore, "_append_index_row", boom)
        with pytest.raises(OSError):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert _snapshot(store.root) == before, "a failed publish left bytes behind"
        assert list((store.root / did / "revisions").iterdir()) == []
        assert store.list_revisions(did) == []
        rev = store.save_revision(did, draft_id=draft["draft_id"])
        assert [r["rev_id"] for r in store.list_revisions(did)] == [rev["rev_id"]]
        assert store.revision_source(did, rev["rev_id"]) == "cube(1);\n"

    def test_index_append_failure_after_the_write_leaves_no_dangling_row(self, store, monkeypatch):
        """Sol (#810 review): a failure *after* the row's bytes were written
        (fsync) must roll the index back too, not only the directory."""

        design = _design(store)
        did = design["design_id"]
        r1 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        draft = _finished_draft(store, did, parent=r1["rev_id"], source="cube(2);\n")
        index = store.root / did / "index.jsonl"
        before_bytes = index.read_bytes()
        before = _snapshot(store.root)
        real_fsync = os.fsync
        calls = {"n": 0}

        def boom(fd):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(5, "Input/output error")
            return real_fsync(fd)

        monkeypatch.setattr(ws.os, "fsync", boom)
        with pytest.raises(OSError):
            store.save_revision(did, draft_id=draft["draft_id"])
        assert index.read_bytes() == before_bytes, "a dangling index row survived the failure"
        assert [r["rev_id"] for r in store.list_revisions(did)] == [r1["rev_id"]]
        assert sorted(p.name for p in (store.root / did / "revisions").iterdir()) == [r1["rev_id"]]
        # the truncate touches the index's mtime; every byte on disk is unchanged
        after = _snapshot(store.root)
        assert set(after) == set(before)
        assert {k: v[1] for k, v in after.items()} == {k: v[1] for k, v in before.items()}
        r2 = store.save_revision(did, draft_id=draft["draft_id"])
        assert [r["rev_id"] for r in store.list_revisions(did)] == [r1["rev_id"], r2["rev_id"]]
        assert len(index.read_text(encoding="utf-8").splitlines()) == 2

    def test_failed_index_rollback_keeps_the_revision_directory(self, store, monkeypatch):
        """Sol (#792/#810 review): when the post-write failure's rollback
        *also* fails, no index row may name a missing revision directory."""

        design = _design(store)
        did = design["design_id"]
        r1 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        draft = _finished_draft(store, did, parent=r1["rev_id"], source="cube(2);\n")
        real_fsync = os.fsync
        calls = {"n": 0}

        def boom(fd):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(5, "Input/output error")
            return real_fsync(fd)

        def no_truncate(path, length):
            raise OSError(30, "Read-only file system")

        monkeypatch.setattr(ws.os, "fsync", boom)
        monkeypatch.setattr(ws.os, "truncate", no_truncate)
        with pytest.raises(OSError, match="Input/output"):
            store.save_revision(did, draft_id=draft["draft_id"])
        monkeypatch.undo()
        rows = store.list_revisions(did)
        dirs = sorted(p.name for p in (store.root / did / "revisions").iterdir())
        # every indexed row names a readable revision directory
        for row in rows:
            assert row["rev_id"] in dirs
            assert store.revision_dir(did, row["rev_id"]).is_dir()
            assert store.read_revision(did, row["rev_id"])["rev_id"] == row["rev_id"]
        # the written row survived the failed truncate, and so did its directory
        assert [r["rev_id"] for r in rows] == [r1["rev_id"], draft_rev_id(rows)]
        assert dirs == sorted([r1["rev_id"], rows[1]["rev_id"]])
        # the next save is an ordinary save: seq 3, no duplicate row, no repair needed
        r3 = store.save_revision(did, draft_id=_finished_draft(store, did, parent=r1["rev_id"], source="cube(3);\n")["draft_id"])
        rows = store.list_revisions(did)
        assert [r["seq"] for r in rows] == [1, 2, 3]
        assert rows[-1]["rev_id"] == r3["rev_id"]
        assert len((store.root / did / "index.jsonl").read_text(encoding="utf-8").splitlines()) == 3

    def test_torn_index_line_is_isolated_and_the_directory_reconciled(self, store):
        """The other half of a failed rollback: a torn last row without its
        newline. The next append starts on a fresh line and the directory is
        indexed again from its revision.json."""

        design = _design(store)
        did = design["design_id"]
        r1 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        r2 = store.save_revision(did, draft_id=_finished_draft(store, did, parent=r1["rev_id"], source="cube(2);\n")["draft_id"])
        index = store.root / did / "index.jsonl"
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        index.write_text(lines[0] + lines[1][:20], encoding="utf-8")  # r2's row torn mid-write
        assert [r["rev_id"] for r in store.list_revisions(did)] == [r1["rev_id"]]
        r3 = store.save_revision(did, draft_id=_finished_draft(store, did, parent=r1["rev_id"], source="cube(3);\n")["draft_id"])
        rows = store.list_revisions(did)
        assert [r["rev_id"] for r in rows] == [r1["rev_id"], r2["rev_id"], r3["rev_id"]]
        assert [r["seq"] for r in rows] == [1, 2, 3]
        raw = index.read_text(encoding="utf-8").splitlines()
        assert len(raw) == 4 and raw[1] == lines[1][:20].rstrip("\n")  # the torn fragment sits alone
        for row in rows:
            assert store.revision_dir(did, row["rev_id"]).is_dir()

    def test_update_draft_source_is_for_unfinished_revise_drafts_only(self, store):
        """W6: a model's answer replaces a revise draft's source and rehashes it,
        merging provenance; edit drafts and finished drafts are never rewritten."""
        design = _design(store)
        did = design["design_id"]
        r1 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        base = store.revision_source(did, r1["rev_id"])
        model = {"kind": "model", "model_id": "stub", "provider": "stub", "confinement": "unconfined", "prompt": "hollow", "max_turns": 40, "reference_images": []}
        revise = store.create_draft(did, parent_rev_id=r1["rev_id"], source=base, kind="revise", editor=model)
        updated = store.update_draft_source(did, revise["draft_id"], "cube(3);\n", editor={"confinement": "not_applicable", "reference_images": ["hero.png"]})
        assert store.draft_source(did, revise["draft_id"]) == "cube(3);\n"
        assert updated["source_sha256"] == ws._sha256_text("cube(3);\n") and updated["source_sha256"] != revise["source_sha256"]
        assert updated["editor"]["confinement"] == "not_applicable" and updated["editor"]["reference_images"] == ["hero.png"]
        assert updated["editor"]["model_id"] == "stub" and updated["editor"]["prompt_sha256"] == revise["editor"]["prompt_sha256"]
        with pytest.raises(ws.WorkbenchError, match="confinement"):
            store.update_draft_source(did, revise["draft_id"], "cube(4);\n", editor={"confinement": "totally"})
        # an edit draft is never rewritten
        edit = store.create_draft(did, parent_rev_id=r1["rev_id"], source=base, editor={"kind": "human", "voter": "t"})
        with pytest.raises(ws.Conflict, match="revise"):
            store.update_draft_source(did, edit["draft_id"], "cube(5);\n")
        assert store.draft_source(did, edit["draft_id"]) == base
        # a finished revise draft is fixed
        store.update_draft_job(did, revise["draft_id"], status="succeeded")
        with pytest.raises(ws.Conflict, match="finished"):
            store.update_draft_source(did, revise["draft_id"], "cube(6);\n")
        assert store.draft_source(did, revise["draft_id"]) == "cube(3);\n"
        # the store refuses an unknown confinement word everywhere, and accepts the new one
        with pytest.raises(ws.WorkbenchError):
            ws.validate_editor({**model, "confinement": "trust-me"})
        assert ws.validate_editor({**model, "confinement": "not_applicable"})["confinement"] == "not_applicable"

    def test_crash_between_rename_and_index_is_reconciled_on_the_next_save(self, store):
        # Simulate a crash after the publish rename: the directory is complete
        # but its index row never landed.
        design = _design(store)
        did = design["design_id"]
        r1 = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        r2 = store.save_revision(did, draft_id=_finished_draft(store, did, parent=r1["rev_id"], source="cube(2);\n")["draft_id"])
        index = store.root / did / "index.jsonl"
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        index.write_text("".join(lines[:1]), encoding="utf-8")  # drop r2's row; its dir stays
        assert [r["rev_id"] for r in store.list_revisions(did)] == [r1["rev_id"]]
        with pytest.raises(Conflict, match="already saved"):
            # the same bytes are still a conflict: the directory exists
            store.save_revision(did, draft_id=_finished_draft(store, did, parent=r1["rev_id"], source="cube(2);\n")["draft_id"])
        r3 = store.save_revision(did, draft_id=_finished_draft(store, did, parent=r1["rev_id"], source="cube(3);\n")["draft_id"])
        rows = store.list_revisions(did)
        assert [r["rev_id"] for r in rows] == [r1["rev_id"], r2["rev_id"], r3["rev_id"]]
        assert [r["seq"] for r in rows] == [1, 2, 3]
        assert store.read_revision(did, r2["rev_id"])["seq"] == 2

    def test_no_staging_directory_survives_a_successful_save(self, store):
        design = _design(store)
        did = design["design_id"]
        rev = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        assert sorted(p.name for p in (store.root / did / "revisions").iterdir()) == [rev["rev_id"]]

    def test_revision_json_is_never_rewritten(self, store):
        design = _design(store)
        did = design["design_id"]
        rev = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        path = store.revision_dir(did, rev["rev_id"]) / "revision.json"
        before = (path.stat().st_mtime_ns, path.read_bytes())
        store.add_curation(did, rev_id=rev["rev_id"], pick=True, title="Best")
        store.read_revision(did, rev["rev_id"])
        store.compare(did, rev["rev_id"], rev["rev_id"])
        assert (path.stat().st_mtime_ns, path.read_bytes()) == before
        assert not hasattr(store, "update_revision") and not hasattr(store, "delete_revision")

    def test_reads_write_nothing(self, store):
        design = _design(store)
        did = design["design_id"]
        rev = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        before = _snapshot(store.root)
        store.list_designs()
        store.get_design(did)
        store.read_revision(did, rev["rev_id"])
        store.revision_source(did, rev["rev_id"])
        store.list_revisions(did)
        store.curation_state(did)
        store.compare(did, rev["rev_id"], rev["rev_id"])
        assert _snapshot(store.root) == before

    def test_compare_diff_and_objective_delta(self, store):
        design = _design(store)
        did = design["design_id"]
        obj_a = {"objective": {"pass_rate": 0.5, "checks": {"min_wall": {"passed": False}, "manifold": {"passed": True}}}}
        obj_b = {"objective": {"pass_rate": 1.0, "checks": {"min_wall": {"passed": True}, "manifold": {"passed": True}}}}
        ra = store.save_revision(did, draft_id=_finished_draft(store, did, source="a = 1;\ncube(a);\n", objective=obj_a)["draft_id"])
        rb = store.save_revision(did, draft_id=_finished_draft(store, did, parent=ra["rev_id"], source="a = 2;\ncube(a);\n", objective=obj_b)["draft_id"])
        cmp = store.compare(did, ra["rev_id"], rb["rev_id"])
        ops = [(r["op"], r["text"]) for r in cmp["diff"]]
        assert ("-", "a = 1;") in ops and ("+", "a = 2;") in ops and (" ", "cube(a);") in ops
        assert cmp["objective_delta"] == {"pass_rate": [0.5, 1.0], "flipped": {"min_wall": [False, True]}}

    def test_source_diff_rows_are_plain_text(self):
        rows = source_diff("x\n<b>y</b>\n", "x\n<i>z</i>\n")
        assert rows[0]["op"] == "@"
        assert any(r["op"] == "-" and r["text"] == "<b>y</b>" for r in rows)


# --- curation and retention ---------------------------------------------------


class TestCurationAndRetention:
    def test_curation_is_append_only_and_last_wins(self, store):
        design = _design(store)
        did = design["design_id"]
        rev = store.save_revision(did, draft_id=_finished_draft(store, did)["draft_id"])
        with pytest.raises(NotFound):
            store.add_curation(did, rev_id="r-missing", pick=True)
        store.add_curation(did, rev_id=rev["rev_id"], pick=True, title="Concert uke", voter="tony")
        store.add_curation(did, note="needs thicker top")
        assert store.curation_state(did) == {"pick": rev["rev_id"], "title": "Concert uke", "note": "needs thicker top", "updated_at": store.curation_history(did)[-1]["created_at"]}
        store.add_curation(did, rev_id=rev["rev_id"], pick=False)
        assert store.curation_state(did)["pick"] is None
        assert len(store.curation_history(did)) == 3
        assert store.list_designs()[0]["title"] == "Concert uke"
        with pytest.raises(TooLarge):
            store.add_curation(did, note="n" * 3000)

    def test_expire_drafts_keeps_running_and_fresh(self, store):
        design = _design(store)
        did = design["design_id"]
        old_done = _finished_draft(store, did)
        old_running = store.create_draft(did, parent_rev_id=None, source="x", editor=HUMAN)
        store.update_draft_job(did, old_running["draft_id"], status="running")
        fresh_done = _finished_draft(store, did, source="cube(9);\n")
        for d in (old_done, old_running):
            p = store.draft_dir(did, d["draft_id"]) / "draft.json"
            payload = json.loads(p.read_text())
            payload["created_at"] = "2020-01-01T00:00:00+00:00"
            p.write_text(json.dumps(payload))
        removed = store.expire_drafts(now=time.time())
        assert removed == [f"{did}/{old_done['draft_id']}"]
        ids = {d["draft_id"] for d in store.list_drafts(did)}
        assert ids == {old_running["draft_id"], fresh_done["draft_id"]}
        # with no grace period the fresh finished draft goes too; running never does
        assert store.expire_drafts(ttl_s=0, now=time.time() + 10) == [f"{did}/{fresh_done['draft_id']}"]
        assert {d["draft_id"] for d in store.list_drafts(did)} == {old_running["draft_id"]}
