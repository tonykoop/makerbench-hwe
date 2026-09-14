"""Unit tests for MakerBench Arena Studio (Issue #696)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")

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
    return TestClient(studio_app, headers={"origin": "http://testserver"})


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


def test_html_ui(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "MakerBench" in response.text
    assert "Arena Studio" in response.text


def test_html_ui_local_assets_all_resolve(client: TestClient):
    """Every asset the Studio SPA references locally must actually be servable (C1/#701)."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    local_refs = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html))
    assert local_refs, "expected the SPA to reference at least one local /static/ asset"
    assert "/static/studio.css" in local_refs
    assert "/static/studio.js" in local_refs

    for ref in local_refs:
        asset_response = client.get(ref)
        assert asset_response.status_code == 200, f"{ref} did not resolve"


def test_html_ui_no_external_urls(client: TestClient):
    """The Studio SPA (HTML + linked CSS/JS) must work fully offline: no CDN/external URLs."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    css_response = client.get("/static/studio.css")
    js_response = client.get("/static/studio.js")
    assert css_response.status_code == 200
    assert js_response.status_code == 200

    for label, text in (
        ("index.html", html),
        ("studio.css", css_response.text),
        ("studio.js", js_response.text),
    ):
        assert "http://" not in text, f"{label} references an external http:// URL"
        assert "https://" not in text, f"{label} references an external https:// URL"


def test_turntable_html_progressive_enhancement(client: TestClient):
    """C2/#698: WebGL orbit must start disabled — the frame turntable is the default."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="btnWebglMode"' in html
    webgl_btn = re.search(r'<button id="btnWebglMode"[^>]*>', html).group(0)
    assert "disabled" in webgl_btn, "WebGL toggle must start disabled until feature-detected"

    assert 'id="btnTurntableMode"' in html
    turntable_btn = re.search(r'<button id="btnTurntableMode"[^>]*>', html).group(0)
    assert "active" in turntable_btn, "frame turntable must be the default active mode"

    # model-viewer is loaded locally only, as an ES module, never from a CDN.
    assert 'type="module" src="/static/assets/model-viewer.min.js"' in html


def test_turntable_js_has_context_loss_safety_net(client: TestClient):
    """C2/#698: WebGL context loss (real CONTEXT_LOST_WEBGL on RDP) must fall back to
    frames automatically, and the fallback path must never be reachable without also
    resetting to the turntable so a voter is never left with a blank viewer.

    A `webglcontextlost` event fired on <model-viewer>'s internal (shadow-DOM) canvas
    is not `composed`, so it never crosses the shadow boundary to reach a window-level
    (or even model-viewer-element-level) listener for that raw event type — a real,
    verified-in-browser defect in an earlier version of this safety net (see
    test_context_loss_error_event_triggers_turntable_fallback for the real-browser
    proof). model-viewer instead re-surfaces internal renderer failures as a plain DOM
    'error' event dispatched on the element itself (light DOM), which a direct
    listener on that element genuinely receives — assert THAT mechanism instead.
    """
    js = client.get("/static/studio.js").text

    assert "isContextLost" in js
    assert "forceTurntableFallback" in js
    # The context-loss safety net listens directly on each <model-viewer> element's
    # own light-DOM 'error' event, not a window-level listener for the raw (and
    # shadow-DOM-scoped, hence unreachable) webglcontextlost event.
    assert "document.getElementById(mvId).addEventListener('error'" in js
    assert "window.addEventListener('webglcontextlost'" not in js
    # The fallback handler must itself flip the UI back to turntable mode.
    fallback_fn = js.split("function forceTurntableFallback", 1)[1].split("\n    }", 1)[0]
    assert "setViewerMode('turntable')" in fallback_fn

    # Auto-rotate and frame preloading (with a visible progress state) are required by
    # the lane contract for the primary turntable view.
    assert "startAutoRotate" in js
    assert "progressEl.style.display" in js


def test_static_js_files_are_syntactically_valid():
    """Every makerbench/arena_studio/static/*.js file must parse as valid JavaScript.

    The HTML contract tests only check that /static/studio.js resolves with a 200 and
    that referenced assets exist — none of them actually parse the JS, so a real syntax
    error (e.g. an escape sequence collapsed into a literal newline inside a Python
    triple-quoted string, which dead-ends the whole page in a browser without ever
    showing up as a Python-side test failure) can slip through silently. Skips instead
    of failing if `node` isn't on PATH — never a required dependency.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available on PATH")

    static_dir = Path(__file__).resolve().parents[1] / "makerbench" / "arena_studio" / "static"
    js_files = sorted(static_dir.glob("*.js"))
    assert js_files, f"expected at least one .js file under {static_dir}"

    for js_file in js_files:
        result = subprocess.run(
            [node, "--check", str(js_file)], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"{js_file.name} failed `node --check`:\n{result.stderr}"
        )


def test_voting_ergonomics_markup(client: TestClient):
    """C4/#703: skip button, undo toast, and per-side defect-checkbox shortcuts must
    all be present in the served HTML."""
    html = client.get("/").text

    assert 'onclick="skipPair()"' in html
    assert 'id="undoToast"' in html
    assert 'onclick="undoLastVote()"' in html

    left_defects = re.findall(
        r'<div class="flags-box" id="flagsLeft".*?</div>\s*</div>', html, re.S
    )
    assert left_defects, "expected the left defect checklist markup"
    for i in ("1", "2", "3"):
        assert f'data-defect-index="{i}"' in left_defects[0]

    # role/aria-label present for a11y (C4 acceptance: focus/ARIA labels).
    assert 'role="group" aria-label="Candidate A defect and disposition checklist"' in html
    assert 'role="group" aria-label="Candidate B defect and disposition checklist"' in html


def test_voting_ergonomics_js_shortcuts(client: TestClient):
    """C4/#703: keyboard voting (A/B/tie/skip), the defect-checkbox shortcut, and the
    undo window must all be wired up in studio.js, and undo must work even without a
    currentPair (e.g. right after the last pair in the queue is voted)."""
    js = client.get("/static/studio.js").text

    for fn in ("skipPair", "undoLastVote", "showUndoToast", "toggleDefectCheckbox"):
        assert f"function {fn}" in js

    keydown_handler = js.split("document.addEventListener('keydown'", 1)[1]
    assert "key === 'u'" in keydown_handler
    # The undo branch must return before the currentPair guard, so 'u' still works once
    # the queue has emptied and currentPair is stale.
    undo_branch, _, rest = keydown_handler.partition("if (!currentPair) return;")
    assert "undoLastVote()" in undo_branch

    assert "skipCursor" in js  # read-only cursor, must reset to 0 after a real vote
    assert "skipCursor = 0" in js


def test_cli_arena_studio_help():
    result = runner.invoke(
        cli_app,
        ["arena", "studio", "--help"],
        env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
    )
    assert result.exit_code == 0
    assert "Launch the MakerBench Arena Studio web interface" in result.stdout
    assert "--allow-remote" in result.stdout


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
