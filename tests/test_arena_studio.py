"""Unit tests for MakerBench Arena Studio (Issue #696)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
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
