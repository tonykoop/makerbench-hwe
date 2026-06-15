"""Tests for ``makerbench/migrate_fingerprint.py`` — the v1->v2 artifact-hash
migration (#222).

The migration is safety-gated: it recomputes ``grade.artifact_sha256`` from the
already-submitted SCAD source and **refuses to write if the recomputed score
moved**. With zero prior test references, a broken guard could silently rewrite
hashes on rows whose score actually changed. These tests pin:

  * ``--check`` (and a stable row) is a no-op,
  * ``MigrationError`` is raised on score drift,
  * the surgical edit only touches the two hash fields, in canonical order.

The OpenSCAD recompute is stubbed (``evaluate``/``load_task`` monkeypatched) so
the guard logic is tested in isolation, deterministically, without a renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from makerbench import migrate_fingerprint as mf
from makerbench.migrate_fingerprint import (
    MigrationError,
    _grade_with_version,
    _scad_source_path,
    migrate_file,
)


@dataclass
class _FakeResult:
    score: int
    artifact_sha256: str | None


class _FakeTask:
    def make_spec(self, seed):
        return {"seed": seed}

    grader = staticmethod(lambda *a, **k: ([], {}))


def _install_recompute(monkeypatch, *, score, new_hash):
    """Stub the OpenSCAD recompute path to return a fixed score/hash."""
    monkeypatch.setattr(mf, "load_task", lambda task_id: _FakeTask())
    monkeypatch.setattr(
        mf, "evaluate",
        lambda attempt, spec, grader, work_dir: _FakeResult(score=score, artifact_sha256=new_hash),
    )


def _write_result(repo_root, *, stored_score, stored_hash="OLDHASH"):
    """Write a one-row result file + its referenced scad source under repo_root."""
    src_rel = "results/m/artifacts/part.scad"
    src_abs = repo_root / src_rel
    src_abs.parent.mkdir(parents=True, exist_ok=True)
    src_abs.write_text("cube([1,1,1]);", encoding="utf-8")

    data = {
        "results": [
            {
                "task_id": "demo",
                "seed": 0,
                "track": "blind",
                "grade": {"score": stored_score, "artifact_sha256": stored_hash},
                "dossier": {
                    "artifacts": [
                        {"role": "source", "format": "scad", "path": src_rel}
                    ]
                },
            }
        ]
    }
    path = repo_root / "results" / "m" / "r_demo.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_scad_source_path_finds_source_artifact():
    row = {
        "dossier": {
            "artifacts": [
                {"role": "render", "format": "png", "path": "x.png"},
                {"role": "source", "format": "scad", "path": "a/b.scad"},
            ]
        }
    }
    assert _scad_source_path(row) == "a/b.scad"


def test_scad_source_path_none_when_no_source():
    assert _scad_source_path({"dossier": {"artifacts": []}}) is None
    assert _scad_source_path({}) is None


def test_grade_with_version_inserts_marker_after_hash():
    grade = {"score": 3, "artifact_sha256": "OLD", "passed": True}
    rebuilt = _grade_with_version(grade, "NEW")
    keys = list(rebuilt.keys())
    assert rebuilt["artifact_sha256"] == "NEW"
    assert rebuilt["artifact_hash_version"] == mf.CANONICAL_HASH_VERSION
    # version marker sits immediately after the hash for a clean diff
    assert keys.index("artifact_hash_version") == keys.index("artifact_sha256") + 1
    # untouched fields preserved
    assert rebuilt["score"] == 3 and rebuilt["passed"] is True


def test_grade_with_version_none_hash_yields_none_version():
    rebuilt = _grade_with_version({"artifact_sha256": "OLD"}, None)
    assert rebuilt["artifact_sha256"] is None
    assert rebuilt["artifact_hash_version"] is None


# --------------------------------------------------------------------------- #
# migrate_file: write, check, no-op, drift guard
# --------------------------------------------------------------------------- #
def test_migrate_writes_new_hash_when_score_stable(tmp_path, monkeypatch):
    path = _write_result(tmp_path, stored_score=5, stored_hash="OLDHASH")
    _install_recompute(monkeypatch, score=5, new_hash="NEWHASH")

    stats = migrate_file(path, repo_root=tmp_path, work_dir=tmp_path / "runs", write=True)
    assert stats["migrated"] == 1
    assert stats["changed"] is True

    row = json.loads(path.read_text())["results"][0]
    assert row["grade"]["artifact_sha256"] == "NEWHASH"
    assert row["grade"]["artifact_hash_version"] == mf.CANONICAL_HASH_VERSION
    # score is never touched
    assert row["grade"]["score"] == 5


def test_check_mode_does_not_write(tmp_path, monkeypatch):
    path = _write_result(tmp_path, stored_score=5, stored_hash="OLDHASH")
    before = path.read_text()
    _install_recompute(monkeypatch, score=5, new_hash="NEWHASH")

    stats = migrate_file(path, repo_root=tmp_path, work_dir=tmp_path / "runs", write=False)
    assert stats["changed"] is True  # would change
    assert path.read_text() == before, "--check must not write the file"


def test_noop_when_hash_already_v2(tmp_path, monkeypatch):
    """A row whose recomputed hash already matches produces no diff."""
    path = _write_result(tmp_path, stored_score=5, stored_hash="SAME")
    # Pre-stamp the version marker so the rebuilt grade is byte-identical.
    data = json.loads(path.read_text())
    data["results"][0]["grade"]["artifact_hash_version"] = mf.CANONICAL_HASH_VERSION
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    before = path.read_text()

    _install_recompute(monkeypatch, score=5, new_hash="SAME")
    stats = migrate_file(path, repo_root=tmp_path, work_dir=tmp_path / "runs", write=True)
    assert stats["changed"] is False
    assert path.read_text() == before


def test_score_drift_raises_migration_error(tmp_path, monkeypatch):
    path = _write_result(tmp_path, stored_score=5, stored_hash="OLDHASH")
    before = path.read_text()
    _install_recompute(monkeypatch, score=2, new_hash="NEWHASH")  # score moved!

    with pytest.raises(MigrationError):
        migrate_file(path, repo_root=tmp_path, work_dir=tmp_path / "runs", write=True)
    # nothing written on the failure path
    assert path.read_text() == before


def test_row_without_scad_source_is_skipped(tmp_path, monkeypatch):
    src_missing = {
        "results": [
            {
                "task_id": "demo",
                "seed": 0,
                "track": "blind",
                "grade": {"score": 5, "artifact_sha256": "OLD"},
                "dossier": {"artifacts": []},
            }
        ]
    }
    path = tmp_path / "r.json"
    path.write_text(json.dumps(src_missing, indent=2) + "\n", encoding="utf-8")
    # evaluate must never be called for a sourceless row
    monkeypatch.setattr(mf, "load_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))

    stats = migrate_file(path, repo_root=tmp_path, work_dir=tmp_path / "runs", write=True)
    assert stats["migrated"] == 0
    assert stats["skipped_no_source"] == 1
    assert stats["changed"] is False
