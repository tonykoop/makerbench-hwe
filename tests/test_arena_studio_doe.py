"""Tests for the Arena Studio DoE Matrix Builder (#697 D3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio import doe
from makerbench import nightly_cad


def test_expand_matrix_full_cross_product():
    cells = doe.expand_matrix(
        ["ocarina", "kora"],
        ["claude-code-opus-5", "codex-gpt-5.6"],
        levels=["L1", "L2"],
        context_tiers=["blind"],
        seeds=[0, 1],
    )
    assert len(cells) == 2 * 2 * 2 * 1 * 2
    assert len({c["cell_id"] for c in cells}) == len(cells)


def test_expand_matrix_is_deduplicated():
    cells_a = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"], seeds=[0])
    cells_b = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"], seeds=[0, 0])
    assert len(cells_a) == 1
    assert len(cells_b) == 1


def test_subscription_model_cost_is_known_zero_not_unknown():
    estimate = doe.estimate_model_cost_and_time(
        "claude-code-opus-5", telemetry_store="/nonexistent/sessions.jsonl"
    )
    assert estimate["cost_usd"] == 0.0
    assert estimate["cost_source"] == "subscription_zero_marginal"


def test_unrecognized_paid_model_cost_stays_unknown_never_zero():
    estimate = doe.estimate_model_cost_and_time(
        "openrouter-some-exotic-model", telemetry_store="/nonexistent/sessions.jsonl"
    )
    assert estimate["cost_usd"] is None
    assert estimate["cost_source"] == "unknown"
    assert estimate["duration_s"] is None


def test_cost_and_time_from_telemetry_store(tmp_path: Path):
    store = tmp_path / "sessions.jsonl"
    records = [
        {
            "session_id": "s1",
            "agent_id": "openrouter-some-exotic-model",
            "duration_seconds": 120.0,
            "telemetry": {"cost_usd": 0.5},
        },
        {
            "session_id": "s2",
            "agent_id": "openrouter-some-exotic-model",
            "duration_seconds": 180.0,
            "telemetry": {"cost_usd": 1.5},
        },
    ]
    store.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    estimate = doe.estimate_model_cost_and_time(
        "openrouter-some-exotic-model", telemetry_store=str(store)
    )
    assert estimate["cost_usd"] == pytest.approx(1.0)
    assert estimate["n_cost_samples"] == 2
    assert estimate["duration_s"] == pytest.approx(150.0)
    assert estimate["n_duration_samples"] == 2


def test_matrix_summary_flags_unknown_cost_and_time():
    cells = [
        {"estimate": {"cost_usd": 0.0, "duration_s": None}},
        {"estimate": {"cost_usd": None, "duration_s": 60.0}},
    ]
    summary = doe.matrix_summary(cells)
    assert summary["n_cells"] == 2
    assert summary["known_cost_usd"] == 0.0
    assert summary["has_unknown_cost_cells"] is True
    assert summary["known_time_s"] == 60.0
    assert summary["has_unknown_time_cells"] is True


def test_build_nightly_queue_groups_by_instrument_seed_context(tmp_path: Path):
    ref_a = tmp_path / "ocarina.png"
    ref_a.write_bytes(b"fake-png")

    cells = doe.expand_matrix(
        ["ocarina"], ["model-a", "model-b"], levels=["L1", "L2"], seeds=[0]
    )
    payload, jobs = doe.build_nightly_queue(
        cells, reference_images={"ocarina": str(ref_a)}, budget_usd=5.0
    )
    assert payload["schema"] == nightly_cad.SCHEMA
    assert len(jobs) == 1
    job = jobs[0]
    assert job.instrument_id == "ocarina"
    # 2 models x 2 levels = 4 entrants in this one job.
    assert len(job.entrants) == 4
    entrant_ids = {e.entrant_id for e in job.entrants}
    assert entrant_ids == {"model-a::L1", "model-a::L2", "model-b::L1", "model-b::L2"}
    job.validate()  # must round-trip through the real schema's own validation


def test_build_nightly_queue_skips_unapproved_and_missing_images(tmp_path: Path):
    cells = doe.expand_matrix(["ocarina", "kora"], ["model-a", "model-b"], levels=["L1"], seeds=[0])
    payload, jobs = doe.build_nightly_queue(
        cells,
        reference_images={"ocarina": str(tmp_path / "missing.png")},
        is_approved=lambda inst: inst == "kora",
    )
    assert jobs == []
    reasons = {s["instrument_id"]: s["reason"] for s in payload["skipped"]}
    assert reasons["ocarina"] == "reference_image_not_approved"
    assert reasons["kora"] == "no_reference_image_on_disk"


def test_build_nightly_queue_skips_single_entrant_groups(tmp_path: Path):
    ref = tmp_path / "ocarina.png"
    ref.write_bytes(b"fake-png")
    cells = doe.expand_matrix(["ocarina"], ["model-a"], levels=["L1"], seeds=[0])
    payload, jobs = doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})
    assert jobs == []
    assert payload["skipped"][0]["reason"] == "fewer_than_two_entrants"


def test_write_queue_file_round_trips_through_load_queue(tmp_path: Path):
    ref = tmp_path / "ocarina.png"
    ref.write_bytes(b"fake-png")
    cells = doe.expand_matrix(["ocarina"], ["model-a", "model-b"], levels=["L1"], seeds=[0])
    payload, jobs = doe.build_nightly_queue(cells, reference_images={"ocarina": str(ref)})

    queue_path = tmp_path / "queue.json"
    doe.write_queue_file(queue_path, payload, jobs)

    loaded_payload, loaded_jobs = nightly_cad.load_queue(queue_path)
    assert loaded_payload["schema"] == nightly_cad.SCHEMA
    assert len(loaded_jobs) == 1
    assert loaded_jobs[0].instrument_id == "ocarina"


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps({"instruments": [{"id": "ocarina", "family": "woodwind"}]}), encoding="utf-8"
    )
    return reg_path


@pytest.fixture
def repo_root_with_reference(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    ref_dir = root / "tasks" / "ocarina"
    ref_dir.mkdir(parents=True)
    (ref_dir / "reference.png").write_bytes(b"fake-png")
    return root


@pytest.fixture
def client(fake_registry: Path, repo_root_with_reference: Path) -> TestClient:
    studio_app = create_studio_app(registry_path=fake_registry, repo_root=repo_root_with_reference)
    # feat/696-arena-studio's same-origin-POST guard (atlas's A1-A4) requires a
    # matching Origin header on every POST; TestClient sends none by default.
    return TestClient(studio_app, headers={"origin": "http://testserver"})


def test_doe_preview_route(client: TestClient):
    response = client.get(
        "/api/doe/preview",
        params={"instruments": "ocarina", "models": "claude-code-opus-5", "levels": "L1,L2", "seeds": "0"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["n_cells"] == 2
    assert all(c["estimate"]["cost_usd"] == 0.0 for c in data["cells"])


def test_doe_queue_route(client: TestClient, repo_root_with_reference: Path):
    response = client.post(
        "/api/doe/queue",
        json={
            "run_id": "doe_test_run",
            "instruments": ["ocarina"],
            "models": ["claude-code-opus-5", "codex-gpt-5.6"],
            "levels": ["L1"],
            "seeds": [0],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["n_jobs"] == 1
    queue_path = Path(data["queue_path"])
    assert queue_path.exists()
    payload, jobs = nightly_cad.load_queue(queue_path)
    assert len(jobs) == 1
