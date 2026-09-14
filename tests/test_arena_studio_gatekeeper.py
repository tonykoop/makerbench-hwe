"""Tests for the Arena Studio reference-image gatekeeper (#697 D4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio import gatekeeper


def test_approve_binds_to_current_image_hash(tmp_path: Path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"version-1")
    approvals: dict = {}
    record = gatekeeper.approve(approvals, "ocarina", image, reviewer="alice")

    assert record.approved is True
    assert record.reviewer == "alice"
    assert record.image_sha256 == gatekeeper.compute_image_hash(image)
    assert gatekeeper.is_approved(approvals, "ocarina", image) is True


def test_approval_invalidates_when_image_bytes_change(tmp_path: Path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"version-1")
    approvals: dict = {}
    gatekeeper.approve(approvals, "ocarina", image)
    assert gatekeeper.is_approved(approvals, "ocarina", image) is True

    image.write_bytes(b"version-2-swapped")  # reviewer approved a different image
    assert gatekeeper.is_approved(approvals, "ocarina", image) is False


def test_approval_invalidates_when_image_deleted(tmp_path: Path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"version-1")
    approvals: dict = {}
    gatekeeper.approve(approvals, "ocarina", image)
    image.unlink()
    assert gatekeeper.is_approved(approvals, "ocarina", image) is False
    assert gatekeeper.is_approved(approvals, "ocarina", None) is False


def test_revoke_clears_approval_but_keeps_hash_on_record(tmp_path: Path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"version-1")
    approvals: dict = {}
    gatekeeper.approve(approvals, "ocarina", image)
    record = gatekeeper.revoke(approvals, "ocarina", reviewer="bob")

    assert record.approved is False
    assert record.reviewer == "bob"
    assert gatekeeper.is_approved(approvals, "ocarina", image) is False


def test_revoke_nonexistent_task_is_a_noop():
    assert gatekeeper.revoke({}, "never-approved") is None


def test_no_record_is_never_approved(tmp_path: Path):
    image = tmp_path / "ref.png"
    image.write_bytes(b"version-1")
    assert gatekeeper.is_approved({}, "ocarina", image) is False


def test_migrate_legacy_approvals_treats_every_entry_as_unapproved():
    legacy = {"ocarina": True, "kora": False, "tongue-drum": True}
    migrated = gatekeeper.migrate_legacy_approvals(legacy)

    assert set(migrated) == set(legacy)
    for task_id, record in migrated.items():
        assert record.approved is False
        assert record.image_sha256 == ""
        assert record.reviewer == gatekeeper.MIGRATION_REVIEWER


def test_load_approvals_migrates_legacy_file_on_disk(tmp_path: Path):
    path = tmp_path / "reference_approvals.json"
    path.write_text(json.dumps({"ocarina": True}), encoding="utf-8")

    loaded = gatekeeper.load_approvals(path)
    assert loaded["ocarina"].approved is False
    assert loaded["ocarina"].reviewer == gatekeeper.MIGRATION_REVIEWER


def test_save_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "reference_approvals.json"
    image = tmp_path / "ref.png"
    image.write_bytes(b"version-1")

    approvals: dict = {}
    gatekeeper.approve(approvals, "ocarina", image)
    gatekeeper.save_approvals(path, approvals)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == gatekeeper.SCHEMA

    reloaded = gatekeeper.load_approvals(path)
    assert reloaded["ocarina"].approved is True
    assert reloaded["ocarina"].image_sha256 == gatekeeper.compute_image_hash(image)


def test_load_missing_file_returns_empty(tmp_path: Path):
    assert gatekeeper.load_approvals(tmp_path / "does_not_exist.json") == {}


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps({"instruments": [{"id": "ocarina", "family": "woodwind"}]}), encoding="utf-8"
    )
    return reg_path


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path / "repo"


@pytest.fixture
def client(fake_registry: Path, repo_root: Path) -> TestClient:
    # feat/696-arena-studio's same-origin-POST guard (atlas's A1-A4) requires a
    # matching Origin header on every POST; TestClient sends none by default.
    return TestClient(
        create_studio_app(registry_path=fake_registry, repo_root=repo_root),
        headers={"origin": "http://testserver"},
    )


def test_service_denies_approval_with_no_image_on_disk(client: TestClient):
    response = client.post("/api/tasks/ocarina/approve?approved=true")
    assert response.status_code == 200
    data = response.json()
    assert data["approved"] is False
    assert "error" in data


def test_service_approval_round_trip_via_routes(client: TestClient, repo_root: Path):
    ref_dir = repo_root / "tasks" / "ocarina"
    ref_dir.mkdir(parents=True)
    (ref_dir / "reference.png").write_bytes(b"real-bytes")

    before = client.get("/api/tasks/ocarina/reference").json()
    assert before["approved"] is False  # existing image alone is not enough (#697 D4)

    approve_res = client.post("/api/tasks/ocarina/approve?approved=true")
    assert approve_res.json()["approved"] is True

    after = client.get("/api/tasks/ocarina/reference").json()
    assert after["approved"] is True

    # Swapping the image bytes silently un-approves it.
    (ref_dir / "reference.png").write_bytes(b"different-bytes-now")
    swapped = client.get("/api/tasks/ocarina/reference").json()
    assert swapped["approved"] is False

    revoke_res = client.post("/api/tasks/ocarina/approve?approved=false")
    assert revoke_res.json()["approved"] is False
