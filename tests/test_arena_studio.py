"""Unit tests for MakerBench Arena Studio (Issue #696)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from makerbench.arena_studio import create_studio_app
from makerbench.cli import app as cli_app

runner = CliRunner()


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    reg_path = tmp_path / "registry.json"
    content = {
        "schema": "makerbench-code-cad-arena-registry-v1",
        "instruments": [
            {
                "id": "ocarina",
                "display_name": "Ocarina",
                "family": "woodwind",
                "task_kind": "single_part_vessel",
                "envelope_mm": [140, 90, 70],
            },
            {
                "id": "kora",
                "display_name": "Kora",
                "family": "strings",
                "task_kind": "harp_lute",
                "envelope_mm": [1200, 350, 250],
            },
        ],
    }
    reg_path.write_text(json.dumps(content), encoding="utf-8")
    return reg_path


@pytest.fixture
def fake_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "test_run"
    run_dir.mkdir(parents=True)

    # Create dummy preview pngs
    png_a = run_dir / "preview_a.png"
    png_b = run_dir / "preview_b.png"
    png_a.write_bytes(b"dummy")
    png_b.write_bytes(b"dummy")

    # Minimal run_log.json
    run_log = {
        "started_at": "2026-09-11T12:00:00Z",
        "config": {
            "model_ids": ["model-a", "model-b"],
            "instruments": ["ocarina"],
        },
        "trials": [
            {
                "trial_id": "trial-1",
                "model_id": "model-a",
                "instrument_id": "ocarina",
                "seed": 0,
                "rep": 0,
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(png_a)},
                },
                "grade": {"compiled": True, "manifold": True},
            },
            {
                "trial_id": "trial-2",
                "model_id": "model-b",
                "instrument_id": "ocarina",
                "seed": 0,
                "rep": 0,
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(png_b)},
                },
                "grade": {"compiled": True, "manifold": True},
            },
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")

    # Initial vote
    vote_record = {
        "pair_id": "pair-12345",
        "winner": "left",
        "voter_id": "tony",
        "voted_at": "2026-09-11T12:05:00Z",
    }
    (run_dir / "votes.blind.jsonl").write_text(json.dumps(vote_record) + "\n", encoding="utf-8")

    revealed_record = {
        "pair_id": "pair-12345",
        "winner": "left",
        "voter_id": "tony",
        "instrument_id": "ocarina",
        "seed": 0,
        "reveal": {
            "left": {"model_id": "model-a"},
            "right": {"model_id": "model-b"},
        },
    }
    (run_dir / "votes.revealed.jsonl").write_text(json.dumps(revealed_record) + "\n", encoding="utf-8")

    return run_dir


@pytest.fixture
def client(fake_run: Path, fake_registry: Path, tmp_path: Path) -> TestClient:
    studio_app = create_studio_app(
        default_run_dir=fake_run,
        registry_path=fake_registry,
        repo_root=tmp_path,
    )
    return TestClient(studio_app)


def test_health_endpoint(client: TestClient, fake_run: Path):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["default_run_dir"] == str(fake_run.resolve())


def test_tasks_endpoint(client: TestClient):
    response = client.get("/api/tasks")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["tasks"][0]["id"] == "ocarina"

    # Test filtering by family
    response_filtered = client.get("/api/tasks?family=strings")
    assert response_filtered.status_code == 200
    data_filtered = response_filtered.json()
    assert data_filtered["count"] == 1
    assert data_filtered["tasks"][0]["id"] == "kora"


def test_runs_discovery(client: TestClient, fake_run: Path):
    response = client.get("/api/runs")
    assert response.status_code == 200
    data = response.json()
    runs = data["runs"]
    assert len(runs) >= 1
    assert any(r["run_id"] == fake_run.name for r in runs)


def test_run_summary(client: TestClient, fake_run: Path):
    response = client.get(f"/api/runs/{fake_run.name}/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == fake_run.name
    assert "model-a" in data["models"]
    assert data["compiled_count"] == 2
    assert data["votes_count"] == 1


def test_run_leaderboard(client: TestClient, fake_run: Path):
    response = client.get(f"/api/runs/{fake_run.name}/leaderboard")
    assert response.status_code == 200
    data = response.json()
    assert "leaderboard" in data
    # At least model-a should be rated with 1 win
    leaders = data["leaderboard"]
    assert len(leaders) >= 1
    assert leaders[0]["entrant"] == "model-a"
    assert leaders[0]["wins"] == 1


def test_run_agreement(client: TestClient, fake_run: Path):
    response = client.get(f"/api/runs/{fake_run.name}/agreement")
    assert response.status_code == 200
    data = response.json()
    assert "rankings" in data


def test_queue_and_vote(client: TestClient, fake_run: Path):
    # Queue query
    response = client.get(f"/api/runs/{fake_run.name}/queue?voter=tony")
    assert response.status_code == 200
    data = response.json()
    assert "done" in data
    assert "total" in data


def test_html_ui(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "MakerBench" in response.text
    assert "Arena Studio" in response.text


def test_cli_arena_studio_help():
    result = runner.invoke(cli_app, ["arena", "studio", "--help"])
    assert result.exit_code == 0
    assert "Launch the MakerBench Arena Studio web interface" in result.stdout


def test_competition_launch_and_status(client: TestClient):
    payload = {
        "run_id": "test_launch_round",
        "instruments": ["ocarina"],
        "models": ["claude-opus-5", "cadam-fable-5.1"],
        "backend": "solidworks-live",
        "context_tier": "image",
        "levels": ["L1", "L2", "L3", "L4"],
        "concurrency": 2,
        "max_turns": 16,
        "timeout_s": 300,
        "seed": 0,
        "skip_image_gate": True,
    }
    response = client.post("/api/competitions/launch", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["run_id"] == "test_launch_round"

    # Verify status endpoint
    status_res = client.get("/api/competitions/status")
    assert status_res.status_code == 200
    jobs = status_res.json().get("jobs") or []
    assert any(j["run_id"] == "test_launch_round" for j in jobs)

    # Verify logs endpoint
    logs_res = client.get("/api/competitions/test_launch_round/logs")
    assert logs_res.status_code == 200
    lines = logs_res.json().get("lines") or []
    assert len(lines) >= 1
    assert any("LAUNCHING ARENA COMPETITION" in line for line in lines)


def test_reference_gatekeeper_and_approval_flow(client: TestClient):
    """Test Story #697: Reference image gatekeeper check and approval flow."""
    # 1. Check initial reference status
    ref_res = client.get("/api/tasks/kora/reference")
    assert ref_res.status_code == 200
    ref_data = ref_res.json()
    assert ref_data["task_id"] == "kora"
    assert "prompt_cmd" in ref_data

    # Prompt endpoint
    prompt_res = client.get("/api/tasks/kora/prompt-reference")
    assert prompt_res.status_code == 200
    assert "agy -p" in prompt_res.json()["prompt_cmd"]

    # 2. Gatekeeper rejection when unapproved
    client.post("/api/tasks/kora/approve?approved=false")
    launch_payload = {
        "run_id": "test_gated_round",
        "instruments": ["kora"],
        "models": ["claude-opus-5", "cadam-fable-5.1"],
        "context_tier": "image",
        "skip_image_gate": False,
    }
    blocked_res = client.post("/api/competitions/launch", json=launch_payload)
    assert blocked_res.status_code == 200
    assert blocked_res.json()["success"] is False
    assert "Visual Reference Gatekeeper" in blocked_res.json()["error"]

    # 3. Approve and retry
    client.post("/api/tasks/kora/approve?approved=true")
    allowed_res = client.post("/api/competitions/launch", json=launch_payload)
    assert allowed_res.status_code == 200
    assert allowed_res.json()["success"] is True


def test_export_winners_and_report(client: TestClient, fake_run: Path):
    """Test Story #699: Winner export and markdown report generation."""
    # Export winners
    exp_res = client.post(f"/api/runs/{fake_run.name}/export-winners")
    assert exp_res.status_code == 200
    data = exp_res.json()
    assert data["success"] is True
    assert data["exported_count"] >= 1

    # Export report
    rep_res = client.get(f"/api/runs/{fake_run.name}/export-report")
    assert rep_res.status_code == 200
    report_text = rep_res.text
    assert f"# MakerBench Arena Studio — Report: {fake_run.name}" in report_text
    assert "Elo Leaderboard" in report_text
    assert "Agreement Analysis" in report_text


def test_vote_with_structured_defect_flags(client: TestClient, fake_run: Path):
    """Test Story #698: Voting with structured defect and disposition flags."""
    # Get pending pair from queue
    queue_res = client.get(f"/api/runs/{fake_run.name}/queue?voter=alice")
    assert queue_res.status_code == 200
    queue_data = queue_res.json()
    assert queue_data.get("has_next") is True
    pair_id = queue_data["current_pair"]["pair_id"]

    # Cast vote with defect checklist and disposition flags
    vote_payload = {
        "pair_id": pair_id,
        "winner": "left",
        "voter": "alice",
        "flags": {
            "left": ["missing_critical_components", "save_for_later"],
            "right": ["wrong_proportions", "delete_immediately"],
        },
    }
    res = client.post(f"/api/runs/{fake_run.name}/vote", json=vote_payload)
    assert res.status_code == 200
    assert res.json()["success"] is True


