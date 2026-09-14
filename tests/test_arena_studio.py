"""Unit tests for MakerBench Arena Studio (Issue #696)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from makerbench.arena_studio import create_studio_app
from makerbench.cli import app as cli_app
from makerbench.redaction import find_host_paths

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
    return TestClient(studio_app, headers={"origin": "http://testserver"})


def test_health_endpoint(client: TestClient, fake_run: Path):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["default_run_dir"] == "<redacted-host-path>"


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
    assert find_host_paths(json.dumps(runs)) == []


def test_extra_run_root_is_opt_in_and_never_publishes_host_path(
    fake_registry: Path, tmp_path: Path
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    external_root = tmp_path / "private-worktree" / "arena_gen"
    external_run = external_root / "external-run"
    external_run.mkdir(parents=True)
    (external_run / "run_log.json").write_text(
        json.dumps({"started_at": "2026-09-13T12:00:00Z", "trials": []}),
        encoding="utf-8",
    )

    without_opt_in = create_studio_app(
        registry_path=fake_registry, repo_root=repo_root
    )
    assert TestClient(without_opt_in).get("/api/runs").json()["runs"] == []

    with_opt_in = create_studio_app(
        registry_path=fake_registry,
        repo_root=repo_root,
        extra_run_roots=[external_root],
    )
    api = TestClient(with_opt_in)
    runs = api.get("/api/runs").json()["runs"]
    assert [run["run_id"] for run in runs] == ["external-run"]
    assert runs[0]["path"] == "<redacted-host-path>"
    assert str(tmp_path) not in json.dumps(runs)
    assert find_host_paths(json.dumps(runs)) == []
    assert api.get("/api/runs/external-run/summary").status_code == 200


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


def test_queue_endpoint_never_leaks_identity_pre_vote(client: TestClient, fake_run: Path):
    """C3/#702: the pre-vote queue payload must be blind.

    candidate_id is the raw trial_id, and trial ids embed entrant/model names (the same
    invariant code_cad_vote_surface.py's page renderer already enforces: "No candidate_id
    in the page markup"). A fresh voter (no prior votes, so a real next_pair is served)
    must never see entrant/model ids, and asset paths must be the anonymized aliases
    under vote_pages/blind/, never a raw path containing the model id.
    """
    response = client.get(f"/api/runs/{fake_run.name}/queue?voter=an-unvoted-fresh-voter")
    assert response.status_code == 200
    data = response.json()
    assert data["current_pair"] is not None, "expected a real pair for a fresh voter"

    raw = json.dumps(data)
    for identity in ("model-a", "model-b", "trial-1", "trial-2"):
        assert identity not in raw, f"{identity} leaked into the pre-vote queue payload"

    assert "candidate_id" not in data["current_pair"]["left"]
    assert "candidate_id" not in data["current_pair"]["right"]

    for side in ("left", "right"):
        render_path = data["current_pair"][side]["render_path"]
        assert "/vote_pages/blind/" in render_path, (
            f"{side} render_path must be a blind-aliased asset, got {render_path}"
        )


def test_root_is_a_ui_free_placeholder(client: TestClient):
    # The API lands without #700's inline UI; the rebuilt frontend ships separately.
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Arena Studio API is running" in response.text
    assert "<script" not in response.text


def test_cli_arena_studio_help():
    result = runner.invoke(
        cli_app,
        ["arena", "studio", "--help"],
        env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
    )
    assert result.exit_code == 0
    assert "Launch the MakerBench Arena Studio web interface" in result.stdout
    assert "--allow-remote" in result.stdout
    assert "--allow-live" in result.stdout


def test_cli_arena_studio_refuses_remote_host_without_opt_in():
    result = runner.invoke(cli_app, ["arena", "studio", "--host", "0.0.0.0"])
    assert result.exit_code == 2
    assert "Refusing a non-loopback" in result.stdout


def test_post_rejects_missing_and_cross_origin(client: TestClient):
    path = "/api/tasks/ocarina/approve?approved=true"
    assert client.post(path, headers={"origin": ""}).status_code == 403
    assert client.post(path, headers={"origin": "https://attacker.example"}).status_code == 403


def test_run_id_cannot_be_a_filesystem_path(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rogue = tmp_path.parent / f"{tmp_path.name}_rogue"
    rogue.mkdir()
    (rogue / "run_log.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)

    # This relative path was accepted by the old Path(run_id).is_dir() shortcut.
    assert client.get(f"/api/runs/{rogue.name}/summary").status_code == 404
    # Encoded absolute paths must not become an alternate run lookup channel.
    encoded = quote(str(rogue), safe="")
    assert client.get(f"/api/runs/{encoded}/summary").status_code == 404


@pytest.mark.parametrize(
    "escape",
    [
        "../../../../../../etc/hostname",
        "..%2F..%2F..%2F..%2F..%2F..%2Fetc%2Fhostname",
        "%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fhostname",
    ],
)
def test_vote_page_asset_refuses_traversal(client: TestClient, fake_run: Path, escape: str):
    response = client.get(f"/runs/{fake_run.name}/vote_pages/{escape}")
    assert response.status_code == 404


def _assert_no_host_path(text: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in text
    assert find_host_paths(text) == []


@pytest.mark.parametrize(
    "path_template",
    [
        "/api/health",
        "/api/runs",
        "/api/tasks",
        "/api/runs/{run}/summary",
        "/api/runs/{run}/leaderboard",
        "/api/runs/{run}/agreement",
        "/api/runs/{run}/queue?voter=a-fresh-voter",
        "/api/tasks/ocarina/reference",
        "/api/tasks/ocarina/prompt-reference",
        "/api/competitions/status",
    ],
)
def test_get_endpoints_never_publish_host_paths(
    client: TestClient, fake_run: Path, tmp_path: Path, path_template: str
):
    # New, unreviewed hardening: #741 redacted discovery and health only; trial
    # artifacts and reference image paths still reached the wire.
    reference = tmp_path / "tasks" / "ocarina" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"png")

    response = client.get(path_template.format(run=fake_run.name))
    assert response.status_code == 200
    _assert_no_host_path(response.text, tmp_path)


def test_error_details_never_publish_host_paths(client: TestClient, tmp_path: Path):
    # A directory named run_log.json is discovered, then fails to load with an
    # OSError whose message names the absolute path.
    (tmp_path / "runs" / "code_cad_arena" / "broken_run" / "run_log.json").mkdir(parents=True)

    response = client.get("/api/runs/broken_run/summary")
    assert response.status_code == 500
    _assert_no_host_path(response.text, tmp_path)


def test_app_minted_urls_are_not_rewritten(client: TestClient, fake_run: Path):
    pair = client.get(f"/api/runs/{fake_run.name}/queue?voter=a-fresh-voter").json()["current_pair"]
    assert pair["left"]["render_path"].startswith(f"/runs/{fake_run.name}/vote_pages/blind/")


def test_competition_launch_and_status(client: TestClient, tmp_path: Path):
    payload = {
        "run_id": "test_launch_round",
        "instruments": ["ocarina"],
        "models": ["stub-a", "stub-b"],
        "backend": "solidworks-live",
        "context_tier": "blind",
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
    assert data["status"] == "running"
    assert data["live"] is False
    assert data["backend"] == "openscad"
    assert data["path"] == "runs/code_cad_arena/test_launch_round"
    _assert_no_host_path(response.text, tmp_path)

    # Verify the real detached dry-run process completes and writes its run log.
    deadline = time.monotonic() + 20
    job = {}
    while time.monotonic() < deadline:
        status_res = client.get("/api/competitions/status?run_id=test_launch_round")
        assert status_res.status_code == 200
        job = status_res.json()
        if job.get("status") != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "completed", client.get(
        "/api/competitions/test_launch_round/logs"
    ).json()
    assert job["run_path"] == "runs/code_cad_arena/test_launch_round"
    _assert_no_host_path(json.dumps(job), tmp_path)
    run_path = tmp_path / job["run_path"]
    run_log = json.loads((run_path / "run_log.json").read_text(encoding="utf-8"))
    assert run_log["summary"]["total_trials"] == 2
    launch = json.loads((run_path / "studio_launch.json").read_text(encoding="utf-8"))
    assert "--stub" in launch["command"]
    assert "solidworks-live" not in launch["command"]

    # Verify logs endpoint
    logs_res = client.get("/api/competitions/test_launch_round/logs")
    assert logs_res.status_code == 200
    lines = logs_res.json().get("lines") or []
    assert len(lines) >= 1
    assert any("ARENA PROCESS START" in line for line in lines)
    assert not any("Preflight checks passed" in line for line in lines)


def test_live_launch_is_refused_without_server_opt_in(client: TestClient):
    response = client.post(
        "/api/competitions/launch",
        json={
            "run_id": "refused_live_round",
            "instruments": ["ocarina"],
            "models": ["claude-opus-5"],
            "backend": "openscad",
            "context_tier": "blind",
            "skip_image_gate": True,
            "live": True,
        },
    )
    assert response.status_code == 403
    assert "--allow-live" in response.json()["detail"]


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
    assert allowed_res.status_code == 500
    assert "local reference images" in allowed_res.json()["detail"]


def test_export_winners_and_report(client: TestClient, fake_run: Path, tmp_path: Path):
    """Test Story #699: Winner export and markdown report generation."""
    # Export winners
    exp_res = client.post(f"/api/runs/{fake_run.name}/export-winners")
    assert exp_res.status_code == 200
    data = exp_res.json()
    assert data["success"] is True
    assert data["exported_count"] >= 1
    assert data["winners"][0]["exported_path"] == "instruments/ocarina/winner.scad"
    _assert_no_host_path(exp_res.text, tmp_path)

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
