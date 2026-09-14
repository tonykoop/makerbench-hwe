"""Unit tests for MakerBench Arena Studio (Issue #696)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from makerbench.arena_studio import create_studio_app
from makerbench.arena_studio.service import ArenaStudioService
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
                    "objective": {
                        "objective_pass_rate": 1.0,
                        "sub_scores": {"watertight": 1.0, "min_wall": 1.0},
                        "passed": True,
                    },
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
                    "objective": {
                        "objective_pass_rate": 0.6,
                        "sub_scores": {"watertight": 1.0, "min_wall": 0.0},
                        "passed": False,
                    },
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
    # Loopback base URL: the TrustedHost guard rejects TestClient's default "testserver".
    return TestClient(
        studio_app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}
    )


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


def test_preflight_endpoint_is_redacted_and_read_only(
    client: TestClient, tmp_path: Path, fake_registry: Path
):
    fake_secret = "sk-FAKE-studio-preflight-never-echo"
    secrets = tmp_path / "nightly.env"
    secrets.write_text(
        "\n".join(
            [
                "CADAM_USER_ID=fake-user",
                f"CADAM_ACCESS_TOKEN={fake_secret}",
                f"SUPABASE_SERVICE_ROLE_KEY={fake_secret}",
            ]
        ),
        encoding="utf-8",
    )
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps({"jobs": [{"job_id": "local-smoke", "status": "queued"}]}),
        encoding="utf-8",
    )
    secrets_before = secrets.read_bytes()
    queue_before = queue.read_bytes()

    response = client.post(
        "/api/preflight",
        json={
            "secrets": str(secrets),
            "queue": str(queue),
            "output_root": str(tmp_path),
            "repo_root": str(tmp_path),
            "runner_script": str(fake_registry),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["verdict"] == "GO"
    assert {item["status"] for item in payload["secrets"]} == {"PRESENT"}
    assert fake_secret not in response.text
    assert secrets.read_bytes() == secrets_before
    assert queue.read_bytes() == queue_before


# Ported from #740 (cedar R2 P4, APPROVE). #740's duplicate /api/preflight implementation
# is dropped in favor of #724's; these API tests pin the same response contract.
def test_preflight_endpoint_redacts_secret_values(client: TestClient, tmp_path: Path):
    """R2 P4: GO/NO-GO + classifications only, never a secret's actual value."""
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text(
        "CADAM_USER_ID=super-secret-user-abc123\n"
        "CADAM_ACCESS_TOKEN=sk-should-never-appear-xyz789\n"
        "SUPABASE_SERVICE_ROLE_KEY=changeme\n",
        encoding="utf-8",
    )
    queue_path = tmp_path / "nightly-cad-queue.json"
    queue_path.write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": []}), encoding="utf-8"
    )
    output_root = tmp_path / "runs"
    output_root.mkdir()

    res = client.post(
        "/api/preflight",
        json={"secrets": str(secrets_path), "queue": str(queue_path), "output_root": str(output_root)},
    )
    assert res.status_code == 200
    data = res.json()

    assert "super-secret-user-abc123" not in res.text
    assert "sk-should-never-appear-xyz789" not in res.text

    by_key = {item["key"]: item["status"] for item in data["secrets"]}
    assert by_key["CADAM_USER_ID"] == "PRESENT"
    assert by_key["CADAM_ACCESS_TOKEN"] == "PRESENT"
    assert by_key["SUPABASE_SERVICE_ROLE_KEY"] == "PLACEHOLDER"  # "changeme" is a stand-in
    assert data["verdict"] == "NO-GO"  # placeholder secret -> not GO
    assert data["lock"]["status"] == "ABSENT"
    assert data["queue"]["ok"] is True
    assert isinstance(data["lines"], list) and len(data["lines"]) > 0


def test_preflight_endpoint_go_verdict_and_paths(client: TestClient, tmp_path: Path):
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text(
        "CADAM_USER_ID=real-user\nCADAM_ACCESS_TOKEN=real-token\nSUPABASE_SERVICE_ROLE_KEY=real-key\n",
        encoding="utf-8",
    )
    queue_path = tmp_path / "nightly-cad-queue.json"
    queue_path.write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": []}), encoding="utf-8"
    )
    output_root = tmp_path / "runs"
    output_root.mkdir()
    runner_script = tmp_path / "run-nightly-cad-arena.ps1"
    runner_script.write_text("# stub", encoding="utf-8")

    res = client.post(
        "/api/preflight",
        json={
            "secrets": str(secrets_path),
            "queue": str(queue_path),
            "output_root": str(output_root),
            "runner_script": str(runner_script),
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["verdict"] == "GO"
    paths_by_name = {p["name"]: p for p in data["paths"]}
    assert paths_by_name["runner_script"]["exists"] is True
    assert paths_by_name["output_root"]["exists"] is True


def test_preflight_endpoint_never_mutates_queue_or_secrets(client: TestClient, tmp_path: Path):
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text("CADAM_USER_ID=x\n", encoding="utf-8")
    queue_path = tmp_path / "nightly-cad-queue.json"
    queue_path.write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": []}), encoding="utf-8"
    )
    output_root = tmp_path / "runs"
    output_root.mkdir()
    secrets_before = secrets_path.read_bytes()
    queue_before = queue_path.read_bytes()

    client.post(
        "/api/preflight",
        json={"secrets": str(secrets_path), "queue": str(queue_path), "output_root": str(output_root)},
    )

    assert secrets_path.read_bytes() == secrets_before
    assert queue_path.read_bytes() == queue_before


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
    assert (
        TestClient(without_opt_in, base_url="http://127.0.0.1").get("/api/runs").json()["runs"]
        == []
    )

    with_opt_in = create_studio_app(
        registry_path=fake_registry,
        repo_root=repo_root,
        extra_run_roots=[external_root],
    )
    api = TestClient(with_opt_in, base_url="http://127.0.0.1")
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


# Ported from #713 (cedar C4, APPROVE after the undo/Elo fix at 2746c35): backend and API
# tests only. The keyboard/markup/JS tests are rewritten against the rebuilt frontend.
def test_skip_cursor_never_mutates_and_wraps(client: TestClient, fake_run: Path):
    """C4/#703 skip ergonomics: `skip` is a pure read-only cursor into the unvoted
    items — must never change `done`/`total`, and must wrap around modulo the unvoted
    count rather than 500ing on an out-of-range value."""
    voter = "cursor-tester"
    base = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    assert base["has_next"] is True
    skippable = base["skippable"]

    same_again = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}&skip=0").json()
    assert same_again["current_pair"]["pair_id"] == base["current_pair"]["pair_id"]
    assert same_again["done"] == base["done"]
    assert same_again["total"] == base["total"]

    wrapped = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}&skip={skippable}").json()
    assert wrapped["current_pair"]["pair_id"] == base["current_pair"]["pair_id"]


def test_undo_vote_retracts_without_mutating_jsonl(client: TestClient, fake_run: Path):
    """C4/#703 undo-last-vote: must append a retraction record (never rewrite/delete a
    line) and make the pair votable again."""
    voter = "undo-tester"
    before = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    pair_id = before["current_pair"]["pair_id"]

    blind_jsonl = fake_run / "votes.blind.jsonl"
    lines_before = blind_jsonl.read_text(encoding="utf-8").splitlines()

    vote_resp = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "left", "voter": voter},
    )
    assert vote_resp.status_code == 200

    after_vote = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    assert after_vote["done"] == before["done"] + 1

    undo_resp = client.post(
        f"/api/runs/{fake_run.name}/undo-vote", json={"pair_id": pair_id, "voter": voter}
    )
    assert undo_resp.status_code == 200
    assert undo_resp.json()["success"] is True

    # The original vote lines are untouched; only new lines were appended.
    lines_after = blind_jsonl.read_text(encoding="utf-8").splitlines()
    assert lines_after[: len(lines_before)] == lines_before
    assert len(lines_after) > len(lines_before)

    retraction_lines = [
        json.loads(line) for line in lines_after[len(lines_before) :]
    ]
    assert any(
        r.get("pair_id") == pair_id and r.get("voter_id") == voter and r.get("retracts") is True
        for r in retraction_lines
    )

    # The pair is votable again.
    after_undo = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    assert after_undo["has_next"] is True
    assert after_undo["current_pair"]["pair_id"] == pair_id
    assert after_undo["done"] == before["done"]

    # Undoing a pair that was never voted (or already undone) is rejected, not silently
    # accepted.
    repeat_undo = client.post(
        f"/api/runs/{fake_run.name}/undo-vote", json={"pair_id": pair_id, "voter": voter}
    )
    assert repeat_undo.status_code == 400


def test_undo_vote_is_not_counted_by_the_production_elo_consumer(client: TestClient, fake_run: Path):
    """C4/#703 fix (post-review): undo must be honored by the SAME consumer the human
    leaderboard reads (code_cad_arena_runner.votes_to_elo_votes), not just the queue.

    An earlier version of undo_vote() retracted only votes.blind.jsonl; the revealed
    stream votes_to_elo_votes() reads kept the original vote live forever, so Elo/
    agreement could still count a vote the UI showed as "undone", and a later revote
    could double-count. Fixed: undo_vote() also appends a retraction to
    votes.revealed.jsonl, and votes_to_elo_votes() replays retractions by
    (pair_id, voter_id) before requiring the reveal identities a plain retraction
    record doesn't carry.
    """
    from makerbench.code_cad_arena_runner import votes_to_elo_votes

    voter = "elo-undo-tester"
    queue = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    pair_id = queue["current_pair"]["pair_id"]

    vote_resp = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "left", "voter": voter},
    )
    assert vote_resp.status_code == 200

    revealed_path = fake_run / "votes.revealed.jsonl"
    votes = votes_to_elo_votes(revealed_path)
    assert any(v.voter_id == voter for v in votes), "vote must be counted before undo"

    undo_resp = client.post(
        f"/api/runs/{fake_run.name}/undo-vote", json={"pair_id": pair_id, "voter": voter}
    )
    assert undo_resp.status_code == 200

    # The production Elo consumer must no longer count the undone vote.
    votes_after_undo = votes_to_elo_votes(revealed_path)
    assert not any(v.voter_id == voter for v in votes_after_undo), (
        "the production Elo consumer still counts a vote the UI reports as undone"
    )

    # A replacement vote for the same pair must count exactly once, not twice.
    revote_resp = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "right", "voter": voter},
    )
    assert revote_resp.status_code == 200
    votes_after_revote = [v for v in votes_to_elo_votes(revealed_path) if v.voter_id == voter]
    assert len(votes_after_revote) == 1, "a revote after undo must count exactly once, not accumulate"
    assert votes_after_revote[0].winner == "right"


# Ported from #737 (cedar R2 P3, APPROVE incl. the per-voter reveal-gate fix 4c580ee):
# run-scoped judge panel API tests. Morning-bundle variants land with the #735 port (B11);
# the panel markup/JS test is rewritten against the rebuilt frontend.
def test_judge_panel_404_before_any_vote(client: TestClient, fake_run: Path):
    """R2 P3: never before the vote — same C3/C4 anonymity invariant."""
    res = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id=pair-not-voted-yet")
    assert res.status_code == 404


def test_judge_panel_after_vote_shows_objective_and_judge_verdict(client: TestClient, fake_run: Path):
    voter = "judge-panel-tester"
    before = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    pair_id = before["current_pair"]["pair_id"]

    vote_resp = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "left", "voter": voter},
    )
    assert vote_resp.status_code == 200

    # Simulate `arena judge` having already run out-of-band (never invoked by us).
    judge_record = {
        "schema": "makerbench-code-cad-judge-v1",
        "pair_id": pair_id,
        "winner": "right",
        "voter_id": "vlm:claude-code-sonnet",
        "judge_model_id": "claude-code-sonnet",
    }
    (fake_run / "votes.judge.jsonl").write_text(json.dumps(judge_record) + "\n", encoding="utf-8")

    res = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter={voter}")
    assert res.status_code == 200
    data = res.json()
    assert data["pair_id"] == pair_id
    assert data["human_winner"] == "left"
    assert data["left"]["model_id"] == "model-a"
    assert data["left"]["objective"]["objective_pass_rate"] == 1.0
    assert data["right"]["model_id"] == "model-b"
    assert data["right"]["objective"]["objective_pass_rate"] == 0.6
    assert data["judge"] == {"winner": "right", "judge_model_id": "claude-code-sonnet"}


def test_judge_panel_omits_judge_block_when_not_yet_judged(client: TestClient, fake_run: Path):
    voter = "no-judge-tester"
    before = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    pair_id = before["current_pair"]["pair_id"]
    client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "draw", "voter": voter},
    )

    res = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter={voter}")
    assert res.status_code == 200
    data = res.json()
    assert data["human_winner"] == "draw"
    assert data["judge"] is None


def test_judge_panel_hides_again_after_undo(client: TestClient, fake_run: Path):
    voter = "undo-then-judge-tester"
    before = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    pair_id = before["current_pair"]["pair_id"]
    client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "left", "voter": voter},
    )
    assert client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter={voter}").status_code == 200

    undo_resp = client.post(
        f"/api/runs/{fake_run.name}/undo-vote", json={"pair_id": pair_id, "voter": voter}
    )
    assert undo_resp.status_code == 200

    res = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter={voter}")
    assert res.status_code == 404


def test_judge_panel_reveal_gate_is_per_voter_not_global(client: TestClient, fake_run: Path):
    """R2 P3/#736 fix (post-review): the reveal gate must be scoped to the
    REQUESTING voter, not "has anyone voted on this pair". An earlier version's
    _pair_is_voted_by_anyone() let a second voter who had never voted on a pair see
    the first voter's identity/objective/judge data just by passing their own
    `voter` query value — a real cross-voter identity leak."""
    alice_queue = client.get(f"/api/runs/{fake_run.name}/queue?voter=alice").json()
    pair_id = alice_queue["current_pair"]["pair_id"]

    alice_vote = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "left", "voter": "alice"},
    )
    assert alice_vote.status_code == 200

    # Bob has NOT voted on this pair. His own queue may hand him the same pair
    # (Swiss pairing is per-voter) — he must not be able to see its reveal yet.
    bob_res = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter=bob")
    assert bob_res.status_code == 404
    assert "alice" not in bob_res.text.lower()

    # Alice herself can see it.
    alice_res = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter=alice")
    assert alice_res.status_code == 200

    # Once Bob also votes on the SAME pair, his own request succeeds and shows
    # his own recorded winner, not a leftover from Alice's vote.
    bob_vote = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "right", "voter": "bob"},
    )
    assert bob_vote.status_code == 200
    bob_res_after = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter=bob")
    assert bob_res_after.status_code == 200
    assert bob_res_after.json()["human_winner"] == "right"


def test_judge_panel_never_calls_a_judge_cli(
    client: TestClient, fake_run: Path, monkeypatch: pytest.MonkeyPatch
):
    """The judge panel only reads what `arena judge` already wrote to disk.

    Ported from #737. Its structural check (`"subprocess" not in dir(service)`)
    cannot hold on this base: B1's honest launcher (#708) imports subprocess to
    run `arena run --stub`. The same guarantee is asserted behaviorally instead:
    every process-spawning entry point is blocked while a real vote -> panel
    round trip runs.
    """
    import os
    import subprocess as subprocess_mod

    import makerbench.arena_studio.service as service_mod

    assert not hasattr(service_mod, "claude_cli_judge")
    assert not hasattr(service_mod, "judge_pair")

    voter = "no-cli-voter"
    queue = client.get(f"/api/runs/{fake_run.name}/queue?voter={voter}").json()
    pair_id = queue["current_pair"]["pair_id"]

    def _blocked(*args, **kwargs):
        raise AssertionError(f"judge panel path tried to spawn a process: {args!r}")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess_mod, name, _blocked)
    monkeypatch.setattr(os, "system", _blocked)

    vote = client.post(
        f"/api/runs/{fake_run.name}/vote",
        json={"pair_id": pair_id, "winner": "left", "voter": voter},
    )
    assert vote.status_code == 200
    panel = client.get(f"/api/runs/{fake_run.name}/judge-panel?pair_id={pair_id}&voter={voter}")
    assert panel.status_code == 200
    assert panel.json()["judge"] is None


# Ported from #733 (nightly cockpit, R2 P1), #735 (morning review, R2 P2, incl. the run_dir
# containment fix 1acfc45) and #737's morning judge panel, all APPROVE: backend API tests only.
# Their tab markup / JS tests are rewritten against the rebuilt frontend.
def _nightly_queue_fixture(repo_root: Path, *, running_job_run_dir: Optional[Path] = None) -> Path:
    """Round 2 P1/#732: a small, committed-style fixture queue (never the real queue).

    Written under repo_root/runs/ — the only root /api/nightly/queue accepts a
    `queue=` override beneath (fixed after review: an earlier version accepted any
    absolute path with no containment check, making the Studio server an oracle over
    arbitrary host files)."""
    runs_dir = repo_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    queue_path = runs_dir / "nightly-cad-queue.json"
    running_job = {
        "job_id": "sambuca-night",
        "instrument_id": "sambuca",
        "reference_image": "tasks/sambuca/reference.png",
        "budget_usd": 5.0,
        "status": "running",
        "run_id": "run-sambuca-01",
        "run_dir": str(running_job_run_dir) if running_job_run_dir else None,
        "entrants": [
            {
                "entrant_id": "cadam-fable-image",
                "kind": "cadam",
                "model_id": "anthropic/claude-fable-5",
                "max_cost_usd": 3.0,
            }
        ],
    }
    queued_job = {
        "job_id": "kora-night",
        "instrument_id": "kora",
        "reference_image": "tasks/kora/reference.png",
        "budget_usd": 2.0,
        "status": "queued",
        "entrants": [
            {"entrant_id": "codex-openscad", "kind": "arena", "model_id": "codex-gpt-5.6-sol"}
        ],
    }
    queue_path.write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": [running_job, queued_job]}),
        encoding="utf-8",
    )
    return queue_path


def test_nightly_queue_endpoint_shape_and_orphan_detection(client: TestClient, tmp_path: Path):
    """R2 P1/#732: read-only nightly cockpit view, no lease file present."""
    queue_path = _nightly_queue_fixture(tmp_path)
    res = client.get(f"/api/nightly/queue?queue={queue_path}")
    assert res.status_code == 200
    data = res.json()
    assert data["schema"] == "makerbench-arena-studio-nightly-view-v1"
    assert data["queue_schema"] == "makerbench-nightly-cad-queue-v1"
    jobs_by_id = {j["job_id"]: j for j in data["jobs"]}
    assert set(jobs_by_id) == {"sambuca-night", "kora-night"}

    # No lease file exists, so a job stuck at status="running" is orphaned.
    assert jobs_by_id["sambuca-night"]["orphaned"] is True
    assert jobs_by_id["sambuca-night"]["budget"] is None
    assert jobs_by_id["kora-night"]["orphaned"] is False

    assert data["lease"]["status"] == "ABSENT"
    assert data["lease"]["pid"] is None


def test_nightly_queue_endpoint_active_lease_clears_orphan_flag(client: TestClient, tmp_path: Path):
    queue_path = _nightly_queue_fixture(tmp_path)
    # The lock path is always derived as queue_path.parent/.nightly-cad.lock (fixed
    # after review: an earlier version accepted an independent `lock=` override with
    # no containment check either).
    lock_path = queue_path.parent / ".nightly-cad.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

    res = client.get(f"/api/nightly/queue?queue={queue_path}")
    assert res.status_code == 200
    data = res.json()
    assert data["lease"]["status"] == "ACTIVE"
    assert data["lease"]["pid"] == os.getpid()
    jobs_by_id = {j["job_id"]: j for j in data["jobs"]}
    assert jobs_by_id["sambuca-night"]["orphaned"] is False


def test_nightly_queue_endpoint_reconstructs_budget_from_run_dir(client: TestClient, tmp_path: Path):
    # Under runs/: a run_dir outside it is never read (see the escape tests below).
    run_dir = tmp_path / "runs" / "sambuca-run"
    run_dir.mkdir(parents=True)
    (run_dir / "nightly-state.json").write_text(
        json.dumps(
            {
                "budget": {
                    "spent_usd": 1.5,
                    "charges": [{"entrant_id": "cadam-fable-image", "cost_usd": 1.5}],
                }
            }
        ),
        encoding="utf-8",
    )
    queue_path = _nightly_queue_fixture(tmp_path, running_job_run_dir=run_dir)

    res = client.get(f"/api/nightly/queue?queue={queue_path}")
    assert res.status_code == 200
    budget = res.json()["jobs"][0]["budget"]
    assert budget["spent_usd"] == 1.5
    assert budget["remaining_usd"] == 3.5
    assert budget["outcomes"] == [{"entrant_id": "cadam-fable-image", "cost_usd": 1.5, "violation": None}]


def test_nightly_queue_endpoint_never_mutates_queue_or_lease(client: TestClient, tmp_path: Path):
    """Cockpit must be strictly read-only: same bytes on disk before and after."""
    queue_path = _nightly_queue_fixture(tmp_path)
    lock_path = queue_path.parent / ".nightly-cad.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    queue_before = queue_path.read_bytes()
    lock_before = lock_path.read_bytes()

    res = client.get(f"/api/nightly/queue?queue={queue_path}")
    assert res.status_code == 200

    assert queue_path.read_bytes() == queue_before
    assert lock_path.read_bytes() == lock_before


def test_nightly_queue_endpoint_404_for_missing_queue(client: TestClient, tmp_path: Path):
    missing = tmp_path / "runs" / "does-not-exist.json"
    res = client.get(f"/api/nightly/queue?queue={missing}")
    assert res.status_code == 404


def test_nightly_queue_endpoint_refuses_queue_outside_allowed_root(client: TestClient, tmp_path: Path):
    """R2 P1/#732 fix (post-review): the Studio server must not become an oracle over
    arbitrary host files. A `queue=` path outside repo_root/runs/ — even one that
    genuinely exists and is a well-formed queue — must be refused, not served."""
    outside_root = tmp_path.parent / "outside-repo-root"
    outside_root.mkdir(exist_ok=True)
    outside_queue = _nightly_queue_fixture(outside_root)

    res = client.get(f"/api/nightly/queue?queue={outside_queue}")
    assert res.status_code == 400
    assert "must be under" in res.text.lower()
    assert str(outside_queue) not in res.text


def test_nightly_queue_endpoint_never_exposes_secrets(client: TestClient, tmp_path: Path):
    """Lease view must go through nightly_preflight.audit_lock's redaction, never a raw dict."""
    queue_path = _nightly_queue_fixture(tmp_path)
    lock_path = queue_path.parent / ".nightly-cad.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid(), "api_key": "sk-should-never-appear"}), encoding="utf-8")

    res = client.get(f"/api/nightly/queue?queue={queue_path}")
    assert res.status_code == 200
    assert "sk-should-never-appear" not in res.text


def _morning_bundle_fixture(
    tmp_path: Path, *, status: str = "votable", write_summary: bool = True, job_id: str = "sambuca-night"
) -> tuple[Path, Path, str]:
    """R2 P2: a nightly job whose morning bundle is ready for review.

    Returns (queue_path, morning_run_dir, job_id). Two candidates in the same
    arena cell so exactly one blind pair is produced (mirrors fake_run's shape).
    `status`/`write_summary` let a test build a job with a run_dir that is NOT yet
    votable, to prove the direct pair/vote/assets routes enforce the same gate
    discovery does (see test_morning_direct_routes_refuse_non_votable_job).
    """
    run_dir = tmp_path / "runs" / "morning_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    png_a = run_dir / "preview_a.png"
    png_b = run_dir / "preview_b.png"
    png_a.write_bytes(b"dummy-a")
    png_b.write_bytes(b"dummy-b")
    run_log = {
        "started_at": "2026-09-13T02:00:00Z",
        "config": {"model_ids": ["cadam-fable-image", "codex-openscad"], "instruments": ["sambuca"]},
        "trials": [
            {
                "trial_id": "trial-morning-a",
                "model_id": "cadam-fable-image",
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "result": {"render_ok": True, "artifacts": {"png_path": str(png_a)}},
            },
            {
                "trial_id": "trial-morning-b",
                "model_id": "codex-openscad",
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "result": {"render_ok": True, "artifacts": {"png_path": str(png_b)}},
            },
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    if write_summary:
        (run_dir / "morning-summary.json").write_text(
            json.dumps(
                {
                    "schema": "makerbench-nightly-cad-morning-v1",
                    "run_id": run_dir.name,
                    "votable": True,
                    "valid_candidate_count": 2,
                    "failed_candidate_count": 0,
                    "pair_files": ["pair-000.html"],
                    "cost_usd": 0.42,
                }
            ),
            encoding="utf-8",
        )

    # Under repo_root/runs/ — the only root /api/nightly/queue and /api/morning/*
    # accept a `queue=` override beneath (#732's containment fix).
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    queue_path = runs_dir / "nightly-cad-queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-queue-v1",
                "jobs": [
                    {
                        "job_id": job_id,
                        "instrument_id": "sambuca",
                        "reference_image": "tasks/sambuca/reference.png",
                        "budget_usd": 5.0,
                        "status": status,
                        "run_id": run_dir.name,
                        "run_dir": str(run_dir),
                        "entrants": [
                            {"entrant_id": "cadam-fable-image", "kind": "cadam", "model_id": "anthropic/claude-fable-5"},
                            {"entrant_id": "codex-openscad", "kind": "arena", "model_id": "codex-gpt-5.6-sol"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return queue_path, run_dir, job_id


def test_morning_direct_routes_refuse_non_votable_job(client: TestClient, tmp_path: Path):
    """R2 P2/#734 fix (post-review): the pair/vote/assets routes must enforce the
    SAME votable gate discovery does. An earlier version only gated
    discover_morning_bundles() (the bundle picker) — a job with a run_dir but
    status="running" (or "queued"/"failed") was still directly reachable via
    /api/morning/{job_id}/pair|vote|assets, bypassing #734's "only after
    finalize_morning_bundle marked it votable" rule."""
    for status in ("queued", "running", "failed"):
        # Reuse the same tmp_path (== client's repo_root) each iteration, not a
        # subdirectory — the queue must live under repo_root/runs/ per #732's
        # containment fix, and each call fully overwrites the fixture's files.
        queue_path, run_dir, job_id = _morning_bundle_fixture(
            tmp_path, status=status, write_summary=False
        )
        assert client.get(f"/api/morning/{job_id}/pair?queue={queue_path}").status_code == 400
        assert (
            client.post(
                f"/api/morning/{job_id}/vote?queue={queue_path}",
                json={"pair_id": "pair-x", "winner": "left", "voter": "tony"},
            ).status_code
            == 400
        )
        assert (
            client.get(f"/api/morning/{job_id}/assets/blind/x.png?queue={queue_path}").status_code
            == 400
        )

    # Also refused when a run_dir HAS a morning-summary.json but the queue's own
    # status field hasn't caught up to "votable" yet (status is authoritative, not
    # file presence alone).
    queue_path, run_dir, job_id = _morning_bundle_fixture(
        tmp_path, status="running", write_summary=True
    )
    assert client.get(f"/api/morning/{job_id}/pair?queue={queue_path}").status_code == 400


def test_morning_direct_routes_refuse_run_dir_outside_runs_root(client: TestClient, tmp_path: Path):
    """Second-review-round fix: the queue *path* is contained under
    repo_root/runs/ (#732), but job.run_dir is a SEPARATE field read from that
    same untrusted queue file, and was never itself contained. A queue entry
    could name an arbitrary external directory as run_dir, mark itself
    votable, and drop a morning-summary.json there — turning the pair/vote/
    assets routes into an arbitrary-file read/write primitive against any path
    the server process can reach. The queue here is validly contained; only
    run_dir points outside runs/."""
    outside_run_dir = tmp_path / "outside-runs-root"
    outside_run_dir.mkdir()
    (outside_run_dir / "morning-summary.json").write_text(
        json.dumps({"schema": "makerbench-nightly-cad-morning-v1", "votable": True}),
        encoding="utf-8",
    )
    # Minimal real run_log.json so the pair/vote routes reach a real decision
    # (not just crash on an unrelated missing file) if containment were absent.
    (outside_run_dir / "run_log.json").write_text(
        json.dumps(
            {
                "started_at": "2026-09-13T02:00:00Z",
                "config": {"model_ids": ["a", "b"], "instruments": ["sambuca"]},
                "trials": [],
            }
        ),
        encoding="utf-8",
    )
    # The assets route resolves under run_dir/vote_pages/ — placing the host
    # file there mirrors exactly what the reviewer's PoC found reachable.
    (outside_run_dir / "vote_pages").mkdir()
    (outside_run_dir / "vote_pages" / "host.txt").write_text(
        "should never be readable via /assets", encoding="utf-8"
    )

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    queue_path = runs_dir / "nightly-cad-queue.json"
    job_id = "escape-attempt"
    queue_path.write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-queue-v1",
                "jobs": [
                    {
                        "job_id": job_id,
                        "instrument_id": "sambuca",
                        "reference_image": "tasks/sambuca/reference.png",
                        "budget_usd": 5.0,
                        "status": "votable",
                        "run_id": "outside-runs-root",
                        "run_dir": str(outside_run_dir),
                        "entrants": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert client.get(f"/api/morning/{job_id}/pair?queue={queue_path}").status_code == 400
    assert (
        client.post(
            f"/api/morning/{job_id}/vote?queue={queue_path}",
            json={"pair_id": "pair-x", "winner": "left", "voter": "tony"},
        ).status_code
        == 400
    )
    res = client.get(f"/api/morning/{job_id}/assets/host.txt?queue={queue_path}")
    assert res.status_code == 400
    assert "should never be readable" not in res.text


_ESCAPES = ["absolute", "dotdot", "symlink"]
_QUERY_ENCODINGS = ["plain", "encoded"]


def _queue_with_external_run_dir(tmp_path: Path, escape: str, *, status: str) -> Path:
    """sol CHANGES, #774: a validly contained queue under repo_root/runs/ whose job
    run_dir points at an external directory holding sentinel data, reached three
    ways. Returns the queue path."""
    outside = tmp_path / "outside-runs-root"
    outside.mkdir(exist_ok=True)
    (outside / "nightly-state.json").write_text(
        json.dumps(
            {"budget": {"spent_usd": 3210.5, "charges": [{"entrant_id": "OUTSIDE_SENTINEL", "cost_usd": 3210.5}]}}
        ),
        encoding="utf-8",
    )
    (outside / "morning-summary.json").write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-morning-v1",
                "votable": True,
                "valid_candidate_count": 987654,
                "failed_candidate_count": 0,
                "cost_usd": 4321.25,
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    if escape == "absolute":
        run_dir = str(outside)
    elif escape == "dotdot":
        run_dir = str(runs_dir / ".." / "outside-runs-root")
    else:
        link = runs_dir / "escape-link"
        link.symlink_to(outside, target_is_directory=True)
        run_dir = str(link)
    queue_path = runs_dir / "nightly-cad-queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-queue-v1",
                "jobs": [
                    {
                        "job_id": "escape-attempt",
                        "instrument_id": "sambuca",
                        "reference_image": "tasks/sambuca/reference.png",
                        "budget_usd": 5.0,
                        "status": status,
                        "run_id": "outside-runs-root",
                        "run_dir": run_dir,
                        "entrants": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return queue_path


def _queue_query(queue_path: Path, encoding: str) -> str:
    return quote(str(queue_path), safe="") if encoding == "encoded" else str(queue_path)


@pytest.mark.parametrize("encoding", _QUERY_ENCODINGS)
@pytest.mark.parametrize("escape", _ESCAPES)
def test_nightly_cockpit_never_replays_budget_from_run_dir_outside_runs_root(
    client: TestClient, tmp_path: Path, escape: str, encoding: str
):
    queue_path = _queue_with_external_run_dir(tmp_path, escape, status="running")
    res = client.get(f"/api/nightly/queue?queue={_queue_query(queue_path, encoding)}")
    assert res.status_code == 200
    assert res.json()["jobs"][0]["budget"] is None
    assert "OUTSIDE_SENTINEL" not in res.text
    assert "3210.5" not in res.text


@pytest.mark.parametrize("encoding", _QUERY_ENCODINGS)
@pytest.mark.parametrize("escape", _ESCAPES)
def test_morning_discovery_never_reads_summary_from_run_dir_outside_runs_root(
    client: TestClient, tmp_path: Path, escape: str, encoding: str
):
    queue_path = _queue_with_external_run_dir(tmp_path, escape, status="votable")
    res = client.get(f"/api/morning/queue?queue={_queue_query(queue_path, encoding)}")
    assert res.status_code == 200
    assert res.json()["bundles"] == []
    assert "987654" not in res.text
    assert "4321.25" not in res.text


def test_morning_bundle_discovery_only_lists_votable_jobs(client: TestClient, tmp_path: Path):
    queue_path, _run_dir, job_id = _morning_bundle_fixture(tmp_path)
    res = client.get(f"/api/morning/queue?queue={queue_path}")
    assert res.status_code == 200
    bundles = res.json()["bundles"]
    assert len(bundles) == 1
    assert bundles[0]["job_id"] == job_id
    assert bundles[0]["votable"] is True
    assert bundles[0]["valid_candidate_count"] == 2


def test_morning_bundle_pair_never_leaks_identity_pre_vote(client: TestClient, tmp_path: Path):
    queue_path, _run_dir, job_id = _morning_bundle_fixture(tmp_path)
    res = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    assert res.status_code == 200
    data = res.json()
    assert data["has_next"] is True
    pair = data["current_pair"]
    body = json.dumps(pair)
    # candidate_id/trial_id/model_id must never reach the wire pre-vote (C3 invariant).
    assert "trial-morning-a" not in body
    assert "trial-morning-b" not in body
    assert "cadam-fable-image" not in body
    assert "codex-openscad" not in body
    assert pair["left"]["render_path"].startswith(f"/api/morning/{job_id}/assets/blind/")
    assert pair["right"]["render_path"].startswith(f"/api/morning/{job_id}/assets/blind/")


# A "/" in a job_id cannot round-trip through any /api/morning/{job_id}/... route:
# Starlette decodes %2F before routing, so the pair route itself 404s (pre-existing,
# logged). Everything a single path segment can carry must work.
@pytest.mark.parametrize("job_id", ["night job?#&x", "nightly α 2026-09-15"])
def test_morning_asset_urls_encode_the_job_id(client: TestClient, tmp_path: Path, job_id: str):
    """Tony (2026-09-15), from the claude UI review of #781: a queue job_id is only required
    to be non-empty, so the asset URLs the pair API mints must encode it. Unencoded, an id
    like "night job?#&x" turns everything after "?" into a query string and the stage's
    images never load."""
    queue_path, _run_dir, job_id = _morning_bundle_fixture(tmp_path, job_id=job_id)
    encoded_job = quote(job_id, safe="")
    res = client.get(f"/api/morning/{encoded_job}/pair?queue={quote(str(queue_path), safe='')}")
    assert res.status_code == 200, res.text
    pair = res.json()["current_pair"]
    for side in ("left", "right"):
        render_path = pair[side]["render_path"]
        assert render_path.startswith(f"/api/morning/{encoded_job}/assets/blind/"), render_path
        asset = client.get(f"{render_path}?queue={quote(str(queue_path), safe='')}")
        assert asset.status_code == 200, (render_path, asset.status_code)
        assert asset.content.startswith(b"dummy-")


def test_morning_bundle_vote_lands_in_votes_blind_jsonl(client: TestClient, tmp_path: Path):
    """Votes land in the same run_dir/votes.blind.jsonl path code_cad_vote_web.py writes."""
    queue_path, run_dir, job_id = _morning_bundle_fixture(tmp_path)
    pair_res = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    pair_id = pair_res.json()["current_pair"]["pair_id"]

    vote_res = client.post(
        f"/api/morning/{job_id}/vote?queue={queue_path}",
        json={"pair_id": pair_id, "winner": "left", "voter": "tony"},
    )
    assert vote_res.status_code == 200
    assert vote_res.json()["success"] is True

    votes_path = run_dir / "votes.blind.jsonl"
    assert votes_path.is_file()
    lines = [json.loads(line) for line in votes_path.read_text(encoding="utf-8").splitlines()]
    assert any(v["pair_id"] == pair_id and v["winner"] == "left" for v in lines)

    # Voting again for the same voter must be refused (queue rebuilds without this pair).
    again = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    assert again.json()["has_next"] is False


@pytest.mark.parametrize(
    "escape",
    [
        "../../../../../../etc/hostname",
        # Starlette normalizes a plain "../" before routing, so only the encoded
        # forms actually reach the route's containment guard (the same finding as
        # the run-scoped vote_pages traversal test above).
        "..%2F..%2F..%2F..%2F..%2F..%2Fetc%2Fhostname",
        "%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fhostname",
        # One level up is a real file inside the run dir: a working escape returns it.
        "..%2Fmorning-summary.json",
    ],
)
def test_morning_bundle_asset_refuses_traversal(client: TestClient, tmp_path: Path, escape: str):
    queue_path, _run_dir, job_id = _morning_bundle_fixture(tmp_path)
    # Populate vote_pages/ by requesting a pair first.
    client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    res = client.get(f"/api/morning/{job_id}/assets/{escape}?queue={queue_path}")
    assert res.status_code == 404


def test_morning_bundle_never_writes_into_morning_vote_dir(client: TestClient, tmp_path: Path):
    """The Studio queue stages into its own vote_pages/, never finalize_morning_bundle's
    morning-vote/ directory — morning.html and morning-vote/*.html stay untouched."""
    queue_path, run_dir, job_id = _morning_bundle_fixture(tmp_path)
    morning_vote_dir = run_dir / "morning-vote"
    morning_vote_dir.mkdir()
    sentinel = morning_vote_dir / "pair-000.html"
    sentinel.write_text("<html>original static page</html>", encoding="utf-8")
    before = sentinel.read_bytes()

    client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")

    assert sentinel.read_bytes() == before
    assert (run_dir / "vote_pages").is_dir()


def test_morning_judge_panel_after_vote(client: TestClient, tmp_path: Path):
    queue_path, run_dir, job_id = _morning_bundle_fixture(tmp_path)
    pair_res = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    pair_id = pair_res.json()["current_pair"]["pair_id"]

    assert (
        client.get(f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&queue={queue_path}").status_code
        == 404
    )

    vote_res = client.post(
        f"/api/morning/{job_id}/vote?queue={queue_path}",
        json={"pair_id": pair_id, "winner": "right", "voter": "tony"},
    )
    assert vote_res.status_code == 200

    res = client.get(f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&queue={queue_path}")
    assert res.status_code == 200
    data = res.json()
    assert data["human_winner"] == "right"
    assert data["judge"] is None
    assert data["left"]["model_id"] in ("cadam-fable-image", "codex-openscad")
    assert data["right"]["model_id"] in ("cadam-fable-image", "codex-openscad")


def test_morning_judge_panel_reveal_gate_is_per_voter_not_global(client: TestClient, tmp_path: Path):
    """R2 P3/#736 fix (post-review), morning surface: same per-voter gate as the
    regular-run judge panel."""
    queue_path, run_dir, job_id = _morning_bundle_fixture(tmp_path)
    pair_res = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}&voter=alice")
    pair_id = pair_res.json()["current_pair"]["pair_id"]

    alice_vote = client.post(
        f"/api/morning/{job_id}/vote?queue={queue_path}",
        json={"pair_id": pair_id, "winner": "left", "voter": "alice"},
    )
    assert alice_vote.status_code == 200

    bob_res = client.get(
        f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&voter=bob&queue={queue_path}"
    )
    assert bob_res.status_code == 404

    alice_res = client.get(
        f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&voter=alice&queue={queue_path}"
    )
    assert alice_res.status_code == 200


# Ported from #747 (cedar R2 P6, APPROVE): the whole nightly-morning cockpit flow composed
# through the API: preflight -> nightly queue -> morning bundle -> anonymous pair -> vote
# -> reveal -> judge panel -> agreement refresh.
def test_studio_full_morning_flow_end_to_end(tmp_path: Path):
    """R2 P6/#… : the whole nightly-morning cockpit flow through one TestClient,
    against small committed-style fixture data (never a real/live run) —
    preflight -> nightly queue -> morning bundle -> anonymous pair -> vote ->
    reveal -> judge panel -> agreement refresh — proving every P1-P4 endpoint
    genuinely composes end to end, not just in isolation.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # A run_dir reachable both by the regular Arena discovery (repo_root/runs/
    # code_cad_arena/<run_id>) and by an absolute path in the nightly queue —
    # a nightly morning bundle's run_dir IS a normal arena run_dir.
    run_id = "e2e-smoke-run"
    run_dir = repo_root / "runs" / "code_cad_arena" / run_id
    run_dir.mkdir(parents=True)
    png_a = run_dir / "a.png"
    png_b = run_dir / "b.png"
    png_a.write_bytes(b"fixture-a")
    png_b.write_bytes(b"fixture-b")
    run_log = {
        "started_at": "2026-09-14T02:00:00Z",
        "config": {"model_ids": ["cadam-fable-image", "codex-openscad"], "instruments": ["sambuca"]},
        "trials": [
            {
                "trial_id": "e2e-trial-a",
                "model_id": "cadam-fable-image",
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(png_a)},
                    "objective": {"objective_pass_rate": 1.0, "sub_scores": {}, "passed": True},
                },
            },
            {
                "trial_id": "e2e-trial-b",
                "model_id": "codex-openscad",
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(png_b)},
                    "objective": {"objective_pass_rate": 0.5, "sub_scores": {}, "passed": False},
                },
            },
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    (run_dir / "morning-summary.json").write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-morning-v1",
                "run_id": run_id,
                "votable": True,
                "valid_candidate_count": 2,
                "failed_candidate_count": 0,
                "cost_usd": 0.75,
            }
        ),
        encoding="utf-8",
    )

    # Nightly queue.json: one votable job pointing at run_dir (P1's cockpit reads
    # this same file), plus a secrets.env for the preflight step.
    job_id = "sambuca-e2e"
    queue_path = repo_root / "runs" / "nightly-cad-queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-queue-v1",
                "jobs": [
                    {
                        "job_id": job_id,
                        "instrument_id": "sambuca",
                        "reference_image": "tasks/sambuca/reference.png",
                        "budget_usd": 5.0,
                        "status": "votable",
                        "run_id": run_id,
                        "run_dir": str(run_dir),
                        "entrants": [
                            {"entrant_id": "cadam-fable-image", "kind": "cadam", "model_id": "anthropic/claude-fable-5"},
                            {"entrant_id": "codex-openscad", "kind": "arena", "model_id": "codex-gpt-5.6-sol"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    secrets_path = repo_root / "nightly-cad-secrets.env"
    secrets_path.write_text(
        "CADAM_USER_ID=e2e-user\nCADAM_ACCESS_TOKEN=e2e-token\nSUPABASE_SERVICE_ROLE_KEY=e2e-key\n",
        encoding="utf-8",
    )
    runner_script = repo_root / "scripts" / "windows" / "run-nightly-cad-arena.ps1"
    runner_script.parent.mkdir(parents=True)
    runner_script.write_text("# fixture stub, never executed", encoding="utf-8")

    app = create_studio_app(repo_root=repo_root)
    # Loopback base URL: B1's TrustedHost guard rejects TestClient's default "testserver".
    client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

    # 1. Preflight: GO, secrets classified but never disclosed.
    preflight_res = client.post(
        "/api/preflight",
        json={
            "secrets": str(secrets_path),
            "queue": str(queue_path),
            "output_root": str(repo_root / "runs"),
            "runner_script": str(runner_script),
        },
    )
    assert preflight_res.status_code == 200
    preflight_data = preflight_res.json()
    assert preflight_data["verdict"] == "GO"
    assert "e2e-user" not in preflight_res.text
    assert "e2e-token" not in preflight_res.text

    # 2. Nightly queue cockpit: the job shows up, not orphaned (no lock file yet
    # means ABSENT lease, and status="votable" != "running" so never flagged).
    nightly_res = client.get(f"/api/nightly/queue?queue={queue_path}")
    assert nightly_res.status_code == 200
    nightly_jobs = {j["job_id"]: j for j in nightly_res.json()["jobs"]}
    assert nightly_jobs[job_id]["status"] == "votable"
    assert nightly_jobs[job_id]["orphaned"] is False

    # 3. Morning bundle discovery.
    bundles_res = client.get(f"/api/morning/queue?queue={queue_path}")
    assert bundles_res.status_code == 200
    bundles = bundles_res.json()["bundles"]
    assert [b["job_id"] for b in bundles] == [job_id]

    # 4. Anonymous pair: no identity on the wire pre-vote.
    pair_res = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    assert pair_res.status_code == 200
    pair_data = pair_res.json()
    pair_id = pair_data["current_pair"]["pair_id"]
    pair_body = json.dumps(pair_data)
    assert "e2e-trial-a" not in pair_body
    assert "e2e-trial-b" not in pair_body
    assert "cadam-fable-image" not in pair_body

    # No judge/objective panel exists before the vote.
    assert (
        client.get(f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&queue={queue_path}").status_code
        == 404
    )

    # 5. Cast the vote.
    vote_res = client.post(
        f"/api/morning/{job_id}/vote?queue={queue_path}",
        json={"pair_id": pair_id, "winner": "left", "voter": "tony"},
    )
    assert vote_res.status_code == 200

    # 5b. Multi-voter boundary (#737 regression, extended per the #747 review):
    # queues are per-voter, so a second voter ("bob") must still receive this
    # SAME unvoted pair, and tony's vote must not unlock the judge/objective
    # panel for bob — only bob's own vote may unlock it for bob.
    bob_pair_res = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}&voter=bob")
    assert bob_pair_res.status_code == 200
    assert bob_pair_res.json()["current_pair"]["pair_id"] == pair_id

    assert (
        client.get(
            f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&queue={queue_path}&voter=bob"
        ).status_code
        == 404
    ), "tony's vote must not unlock the judge panel for a different voter (bob)"

    bob_vote_res = client.post(
        f"/api/morning/{job_id}/vote?queue={queue_path}",
        json={"pair_id": pair_id, "winner": "right", "voter": "bob"},
    )
    assert bob_vote_res.status_code == 200

    bob_judge_res = client.get(
        f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&queue={queue_path}&voter=bob"
    )
    assert bob_judge_res.status_code == 200
    assert bob_judge_res.json()["human_winner"] == "right"

    # 6. Reveal: the pair is no longer in the unvoted queue.
    after_vote = client.get(f"/api/morning/{job_id}/pair?queue={queue_path}")
    assert after_vote.json()["has_next"] is False
    assert after_vote.json()["done"] == 1

    # Seed a judge verdict, simulating an earlier out-of-band `arena judge` run —
    # this endpoint never calls a judge CLI itself. Reuse the real reveal block
    # votes.revealed.jsonl already has for this pair (rather than guessing which
    # model landed left/right, which the blind shuffle hides) — this is exactly
    # the shape code_cad_judge.judge_pair() itself produces via reveal_vote().
    revealed_line = json.loads(
        (run_dir / "votes.revealed.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    (run_dir / "votes.judge.jsonl").write_text(
        json.dumps(
            {
                "schema": "makerbench-code-cad-judge-v1",
                "pair_id": pair_id,
                "winner": "left",
                "voter_id": "vlm:claude-code-sonnet",
                "judge_model_id": "claude-code-sonnet",
                "reveal": revealed_line["reveal"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # 7. Judge & objective panel: now visible, shows both the objective gate
    # result recorded on each trial and the judge verdict just seeded.
    judge_res = client.get(f"/api/morning/{job_id}/judge-panel?pair_id={pair_id}&queue={queue_path}")
    assert judge_res.status_code == 200
    judge_data = judge_res.json()
    assert judge_data["human_winner"] == "left"
    assert judge_data["judge"] == {"winner": "left", "judge_model_id": "claude-code-sonnet"}
    objective_rates = {judge_data["left"]["objective"]["objective_pass_rate"], judge_data["right"]["objective"]["objective_pass_rate"]}
    assert objective_rates == {1.0, 0.5}

    # 8. Agreement refresh: both votes cast through Morning Review (tony's and
    # bob's, from 5b) feed the same Elo/agreement math the regular Arena
    # Analytics tab reads — proving Morning Review isn't a parallel/disconnected
    # data path, for more than one voter.
    agreement_res = client.get(f"/api/runs/{run_id}/agreement")
    assert agreement_res.status_code == 200
    summary_res = client.get(f"/api/runs/{run_id}/summary")
    assert summary_res.status_code == 200
    assert summary_res.json()["votes_count"] == 2


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


@pytest.mark.parametrize("hostile_host", ["attacker.example", "attacker.example:8080", "testserver"])
def test_dns_rebinding_host_header_is_refused(client: TestClient, hostile_host: str):
    # New, unreviewed hardening: a page that rebinds its own hostname to 127.0.0.1
    # passes the loopback bind, and the same-origin POST guard compares Origin to
    # that same attacker Host. Only an allowlisted Host header may reach the API.
    assert client.get("/api/runs", headers={"host": hostile_host}).status_code == 400
    rebound_post = client.post(
        "/api/tasks/ocarina/approve?approved=true",
        headers={"host": hostile_host, "origin": f"http://{hostile_host}"},
    )
    assert rebound_post.status_code == 400
    assert client.get("/api/runs", headers={"host": "localhost:8080"}).status_code == 200


def test_cli_loopback_bind_keeps_the_rebinding_guard(monkeypatch: pytest.MonkeyPatch):
    import uvicorn

    served: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: served.update(app=app, **kwargs))

    for flags in ([], ["--allow-remote"]):
        served.clear()
        result = runner.invoke(cli_app, ["arena", "studio", *flags])
        assert result.exit_code == 0, result.stdout
        api = TestClient(served["app"], base_url="http://attacker.example")
        assert api.get("/api/health").status_code == 400
        assert TestClient(served["app"], base_url="http://127.0.0.1").get("/api/health").status_code == 200


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


def test_sse_endpoint_tails_existing_log(client: TestClient, fake_run: Path):
    (fake_run / "arena_test.log").write_text("first\nsecond\n", encoding="utf-8")

    response = client.get(
        f"/api/competitions/{fake_run.name}/logs/stream?tail=1&follow=false"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == 'data: "second"\n\n'


def test_sse_stream_never_publishes_host_paths(
    client: TestClient, fake_run: Path, tmp_path: Path
):
    # New, unreviewed: extends the API-boundary host-path redaction to SSE events.
    (fake_run / "arena_test.log").write_text(
        f"wrote {fake_run / 'model.stl'}\nread /home/someone/private/ref.png\n",
        encoding="utf-8",
    )

    response = client.get(
        f"/api/competitions/{fake_run.name}/logs/stream?tail=5&follow=false"
    )

    assert response.status_code == 200
    _assert_no_host_path(response.text, tmp_path)
    events = [
        json.loads(chunk.removeprefix("data: "))
        for chunk in response.text.strip().split("\n\n")
    ]
    assert events == ["wrote test_run/model.stl", "read <redacted-host-path>"]


def test_server_restart_rediscovers_live_detached_job(tmp_path: Path, fake_registry: Path):
    run_path = tmp_path / "runs" / "code_cad_arena" / "recovered-run"
    run_path.mkdir(parents=True)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "makerbench.cli", str(run_path)]
    )
    try:
        launch = {
            "schema": "makerbench-arena-studio-launch-v1",
            "run_id": run_path.name,
            "run_path": str(run_path),
            "log_path": str(run_path / "arena_recovered-run.log"),
            "status": "running",
            "pid": process.pid,
            "progress": "0/2",
        }
        (run_path / "studio_launch.json").write_text(json.dumps(launch), encoding="utf-8")

        service = ArenaStudioService(registry_path=fake_registry, repo_root=tmp_path)
        status = service.get_competition_status(run_path.name)

        assert status["status"] == "running"
        assert status["pid"] == process.pid
        assert status["run_path"] == str(run_path)
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_server_restart_marks_dead_unfinished_job_interrupted(
    tmp_path: Path, fake_registry: Path
):
    run_path = tmp_path / "runs" / "code_cad_arena" / "dead-run"
    run_path.mkdir(parents=True)
    launch = {
        "schema": "makerbench-arena-studio-launch-v1",
        "run_id": run_path.name,
        "status": "running",
        "pid": 2_147_483_647,
        "progress": "0/2",
    }
    launch_path = run_path / "studio_launch.json"
    launch_path.write_text(json.dumps(launch), encoding="utf-8")

    service = ArenaStudioService(registry_path=fake_registry, repo_root=tmp_path)
    status = service.get_competition_status(run_path.name)

    assert status["status"] == "interrupted"
    assert json.loads(launch_path.read_text(encoding="utf-8"))["status"] == "interrupted"


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


def test_reference_gatekeeper_and_approval_flow(client: TestClient, tmp_path: Path):
    """Test Story #697: Reference image gatekeeper check and approval flow.

    #697 D4 binds approval to the reference image's sha256 — approving
    requires a real file on disk to hash, so this test provides one before
    step 3 (it did not need one under the old plain {task: bool} approvals).
    """
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

    # 3. Approve and retry (needs a real reference image to hash-bind to)
    kora_ref = tmp_path / "tasks" / "kora" / "reference.png"
    kora_ref.parent.mkdir(parents=True, exist_ok=True)
    kora_ref.write_bytes(b"fake-png-bytes")
    approve_res = client.post("/api/tasks/kora/approve?approved=true")
    assert approve_res.json()["approved"] is True
    # With a hash-approved image on disk, B1's honest launcher starts a real
    # zero-token --stub dry run instead of refusing for a missing image.
    allowed_res = client.post("/api/competitions/launch", json=launch_payload)
    assert allowed_res.status_code == 200
    assert allowed_res.json()["success"] is True
    assert allowed_res.json()["live"] is False
    deadline = time.monotonic() + 20
    job = {}
    while time.monotonic() < deadline:
        job = client.get("/api/competitions/status?run_id=test_gated_round").json()
        if job.get("status") != "running":
            break
        time.sleep(0.1)
    assert job["status"] != "running"


def test_reference_image_is_served_only_for_registry_tasks(client: TestClient, tmp_path: Path):
    # New, unreviewed: Studio shows the image a person is asked to approve.
    image = tmp_path / "tasks" / "kora" / "reference.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG-kora")
    # Where tasks/<id>/../reference.png lands for a crafted ".." id.
    (tmp_path / "reference.png").write_bytes(b"\x89PNG-outside-any-task")

    response = client.get("/api/tasks/kora/reference/image")
    assert response.status_code == 200
    assert response.content == b"\x89PNG-kora"
    assert response.headers["cache-control"] == "no-store"

    assert client.get("/api/tasks/ocarina/reference/image").status_code == 404  # no image yet
    assert client.get("/api/tasks/not-in-registry/reference/image").status_code == 404
    crafted = client.get("/api/tasks/%2E%2E/reference/image")
    assert crafted.status_code == 404
    assert b"outside-any-task" not in crafted.content


def _registry_with_ids(tmp_path: Path, *ids: str) -> Path:
    reg_path = tmp_path / "hostile-registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "schema": "makerbench-code-cad-arena-registry-v1",
                "instruments": [
                    {"id": task_id, "display_name": task_id, "family": "strings", "envelope_mm": [1, 1, 1]}
                    for task_id in ids
                ],
            }
        ),
        encoding="utf-8",
    )
    return reg_path


@pytest.mark.parametrize("task_path", ["..", "%2E%2E"])
def test_reference_image_rejects_configured_dotdot_registry_id(
    fake_run: Path, tmp_path: Path, task_path: str
):
    # Sol CHANGES #778: registry membership is not an id rule. A registry that
    # itself lists ".." must still never resolve tasks/../reference.png.
    (tmp_path / "reference.png").write_bytes(b"\x89PNG-outside-any-task")
    registry = _registry_with_ids(tmp_path, "kora", "..")
    service = ArenaStudioService(registry_path=registry, repo_root=tmp_path)
    assert ".." in {task["id"] for task in service.get_registry_tasks()}

    assert service.reference_image_path("..") is None
    assert service.get_task_reference("..")["image_path"] is None
    assert service.set_task_approval("..", True)["approved"] is False
    assert service.set_task_approval("..", False)["error"] == "invalid task id"

    studio_app = create_studio_app(default_run_dir=fake_run, registry_path=registry, repo_root=tmp_path)
    local = TestClient(studio_app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
    # The id rule itself answers, before any path join: every task route is a 404.
    for url in (
        f"/api/tasks/{task_path}/reference/image",
        f"/api/tasks/{task_path}/reference",
        f"/api/tasks/{task_path}/prompt-reference",
    ):
        response = local.get(url)
        assert response.status_code == 404, url
        assert b"outside-any-task" not in response.content
    assert local.post(f"/api/tasks/{task_path}/approve?approved=false").status_code == 404
    approvals = tmp_path / ".makerbench" / "reference_approvals.json"
    assert not approvals.exists() or '".."' not in approvals.read_text(encoding="utf-8")
    # A valid registry id still works on the same app.
    assert local.get("/api/tasks/kora/reference").status_code == 200


def test_reference_image_rejects_symlink_out_of_tasks_root(fake_run: Path, tmp_path: Path):
    # The resolved image path is contained, not just the joined one.
    outside = tmp_path / "outside" / "secret.png"
    outside.parent.mkdir()
    outside.write_bytes(b"\x89PNG-outside-any-task")
    link = tmp_path / "tasks" / "kora" / "reference.png"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    registry = _registry_with_ids(tmp_path, "kora")
    studio_app = create_studio_app(default_run_dir=fake_run, registry_path=registry, repo_root=tmp_path)
    local = TestClient(studio_app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

    response = local.get("/api/tasks/kora/reference/image")
    assert response.status_code == 404
    assert b"outside-any-task" not in response.content
    service = ArenaStudioService(registry_path=registry, repo_root=tmp_path)
    assert service.set_task_approval("kora", True)["approved"] is False


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


@pytest.mark.parametrize("hostile_id", ["../../escaped", "..", "nested/dir", "/abs/escaped"])
def test_export_winners_never_writes_outside_instruments(
    client: TestClient, fake_run: Path, tmp_path: Path, hostile_id: str
):
    # New, unreviewed hardening: instrument_id comes from run_log.json and was
    # joined straight into repo_root/instruments/<id>.
    run_log_path = fake_run / "run_log.json"
    run_log = json.loads(run_log_path.read_text(encoding="utf-8"))
    for trial in run_log["trials"]:
        trial["instrument_id"] = hostile_id
    run_log_path.write_text(json.dumps(run_log), encoding="utf-8")

    response = client.post(f"/api/runs/{fake_run.name}/export-winners")

    assert response.status_code == 200
    data = response.json()
    assert data["exported_count"] == 0
    assert data["skipped"] == [{"instrument_id": hostile_id, "reason": "unsafe instrument id"}]
    # Every place each hostile id would have landed stays untouched.
    assert not (tmp_path / "winner.scad").exists()
    assert not (tmp_path.parent / "escaped").exists()
    assert not Path("/abs/escaped").exists()
    instruments = tmp_path / "instruments"
    assert not instruments.exists() or list(instruments.rglob("winner.scad")) == []


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
