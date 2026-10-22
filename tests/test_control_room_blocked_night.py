"""Control Room Morning Review on a blocked night (#1158): the night banner, the status chip,
the full failure text and transcripts per failed card, and *Retry this entrant*."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("trimesh")

from fastapi.testclient import TestClient  # noqa: E402

from control_room_fixture import BLOCKED_RUN, SAMBUCA_RUN, add_blocked_night, build_control_room_repo  # noqa: E402

from makerbench import nightly_revisions  # noqa: E402
from makerbench.arena_studio import control_room as cr  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}
FREECAD = "sambuca__seed0__rep0__freecad-opus"
SOLIDWORKS = "sambuca__seed0__rep0__solidworks-sol"
FUSION = "sambuca__seed0__rep0__fusion-sonnet"
CADAM = "sambuca__seed0__rep0__cadam-opus"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = build_control_room_repo(tmp_path)
    add_blocked_night(repo)
    return repo


def _client(repo: Path, **kwargs) -> TestClient:
    return TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, **kwargs), base_url="http://127.0.0.1")


def _run(repo: Path) -> Path:
    return repo / "runs" / "nightly" / BLOCKED_RUN


def test_night_picker_carries_the_runners_verdict(repo: Path):
    nights = _client(repo).get("/api/review/nights").json()["nights"]
    by_id = {night["run_id"]: night for night in nights}
    assert nights[0]["run_id"] == BLOCKED_RUN  # newest first
    assert by_id[BLOCKED_RUN]["status"] == "blocked" and by_id[BLOCKED_RUN]["valid"] == 0 and by_id[BLOCKED_RUN]["failed"] == 3
    assert by_id[SAMBUCA_RUN]["status"] == "partial"  # votable, one entrant errored


def test_night_state_reads_the_summary_not_the_designs():
    assert cr.night_state({"votable": True, "failed_candidate_count": 0}, {"status": "votable"}) == "votable"
    assert cr.night_state({"votable": True, "failed_candidate_count": 2}, {}) == "partial"
    assert cr.night_state({"votable": False}, {}) == "blocked"
    assert cr.night_state({"votable": True, "safety_block": "halted"}, {}) == "blocked"
    assert cr.night_state({"votable": True}, {"status": "running"}) == "running"


def test_blocked_view_lists_every_failed_entrant_with_its_own_reason(repo: Path):
    view = cr.review_view(_run(repo), repo)
    summary = view["summary"]
    assert summary["status"] == "blocked" and summary["votable"] is False and summary["valid_candidates"] == 0
    assert summary["safety_block"].startswith("paid CADAM dispatch has no persisted structured build_parametric_model result")
    reasons = {entry["entrant"]: entry["reason"] for entry in summary["failed_entrants"]}
    assert reasons == {
        "freecad-opus": "FreeCAD compile failed: AttributeError: 'Part.Arc' object has no attribute 'Center'",
        "solidworks-sol": "live solidworks-live agent produced no STL at arena-live/sambuca__seed0__rep0__solidworks-sol/"
                          "sambuca_base_r1/sambuca_base_r1_model.stl (found nothing; driver exit 0)",
        "fusion-sonnet": "graded, but no preview render was written, so it never entered the morning vote",
        "cadam-opus": "Insufficient credits. This account never purchased credits. Make sure your key is on the correct "
                      "account or org, and if so, purchase more at https://openrouter.ai/settings/credits",
    }
    assert summary["spend"] == {"spent_usd": 0.0, "limit_usd": 2.0, "reserved_usd": 2.0, "halted_reason": None}
    assert summary["next_action"] == "Add OpenRouter credits, then use Retry this entrant on the CADAM card."
    # The CADAM entrant never got a trial row: its card is a placeholder, ranked last.
    designs = {design["trial_id"]: design for design in view["designs"]}
    assert len(designs) == 4 and designs[CADAM]["placeholder"] is True and designs[CADAM]["graded"] is False
    assert view["grader_order"][-1] == CADAM and view["grader_order"][0] == FUSION
    assert designs[CADAM]["failures"] == [f"No graded design: {summary['safety_block']}"]
    assert "From the CADAM stream:\nerror: Insufficient credits" not in designs[CADAM]["failure_detail"]
    assert "From the CADAM stream:\nInsufficient credits" in designs[CADAM]["failure_detail"]
    # The FreeCAD card shows the whole traceback on request; the one-liner is the exception.
    assert designs[FREECAD]["failures"] == ["No graded design: FreeCAD compile failed: AttributeError: 'Part.Arc' object has no attribute 'Center'"]
    assert "Traceback (most recent call last)" in designs[FREECAD]["failure_detail"]
    assert designs[FREECAD]["failure_detail"].endswith("(failed in stage: openscad_render)")
    # Fusion was graded (all five checks) but is honest about not being in the vote.
    assert designs[FUSION]["graded"] is True and designs[FUSION]["has_mesh"] is True and designs[FUSION]["has_render"] is False
    assert designs[FUSION]["failures"] == ["Not in the morning vote: no preview render was written for this design."]
    # Only the texts that exist are linked.
    assert designs[FREECAD]["transcripts"] == []
    assert designs[SOLIDWORKS]["transcripts"] == [{"kind": "transcript", "label": "build_transcript.txt"}]
    assert designs[CADAM]["transcripts"] == [{"kind": "cadam-stream", "label": "cadam-stream.bin (decoded)"}]
    assert all(design["retry"]["runner"] == "unsupported" for design in designs.values())  # none is an OpenSCAD arena entrant


def test_votable_fixture_nights_are_unchanged_by_the_banner_fields(repo: Path):
    view = cr.review_view(repo / "runs" / "nightly" / SAMBUCA_RUN, repo)
    assert view["summary"]["status"] == "partial" and view["summary"]["next_action"].startswith("Review the graded designs")
    assert [entry["entrant"] for entry in view["summary"]["failed_entrants"]] == ["fusion-sonnet"]
    assert all(design["placeholder"] is False for design in view["designs"])
    assert cr.retry_support("arena", "openscad")["runner"] == "supported"


def test_text_routes_serve_transcripts_and_decoded_streams_only(repo: Path):
    client = _client(repo)
    base = f"/api/review/{BLOCKED_RUN}/text"
    transcript = client.get(f"{base}/{SOLIDWORKS}/transcript")
    assert transcript.status_code == 200 and transcript.headers["content-type"].startswith("text/plain")
    assert "SolidWorks does not expose NewDocument" in transcript.text
    assert transcript.headers["cache-control"] == "no-store"
    stream = client.get(f"{base}/{CADAM}/cadam-stream").text
    assert stream.splitlines() == [
        "start",
        "error: Insufficient credits. This account never purchased credits. Make sure your key is on the correct account "
        "or org, and if so, purchase more at https://openrouter.ai/settings/credits",
        "error: Model call failed (openrouter/anthropic/claude-opus-5.5): No output generated. Check the stream for errors.",
        "[DONE]",
    ]
    assert "messageId" not in stream  # only text and error text leave the file
    assert client.get(f"{base}/{FREECAD}/transcript").status_code == 404
    assert client.get(f"{base}/{SOLIDWORKS}/cadam-stream").status_code == 404
    assert client.get(f"{base}/{SOLIDWORKS}/assignment").status_code == 404
    assert client.get(f"{base}/nope/transcript").status_code == 404
    assert client.get(f"{base}/..%2F..%2Fbrief.json/transcript").status_code == 404


def test_a_symlinked_transcript_is_never_served(repo: Path, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET", encoding="utf-8")
    path = _run(repo) / "gen" / SOLIDWORKS / "build_transcript.txt"
    path.unlink()
    path.symlink_to(outside)
    client = _client(repo)
    assert client.get(f"/api/review/{BLOCKED_RUN}/text/{SOLIDWORKS}/transcript").status_code == 404
    design = next(d for d in client.get(f"/api/review/{BLOCKED_RUN}").json()["designs"] if d["trial_id"] == SOLIDWORKS)
    assert design["transcripts"] == []


def test_decode_cadam_stream_keeps_ids_in_the_file():
    raw = b'data: {"type":"start","messageId":"abc"}\n\ndata: {"type":"text","text":"hello"}\n\nnoise\n\ndata: [DONE]\n'
    assert cr.decode_cadam_stream(raw) == "start\ntext: hello\nnoise\n[DONE]\n"
    assert cr.decode_cadam_stream(b"") == ""


def test_retry_keeps_a_draft_when_queue_writes_are_off(repo: Path):
    client = _client(repo)
    assert client.post(f"/api/review/{BLOCKED_RUN}/retry/{CADAM}", json={}).status_code == 403  # same-origin rule
    result = client.post(f"/api/review/{BLOCKED_RUN}/retry/{CADAM}", json={}, headers=ORIGIN).json()
    assert result["queued"] is False and result["draft"] == cr.RETRY_DRAFT_FILE and result["runner"]["runner"] == "unsupported"
    job = result["job"]
    assert job["job_id"] == "cr-20261018T185726Z-sambuca-retry-cadam-opus" and job["retry"] is True and job["status"] == "queued"
    assert job["entrants"] == [{"entrant_id": "cadam-opus"}] and job["instrument_id"] == "sambuca"
    assert job["revision_of"]["feedback"].startswith("Retry after: paid CADAM dispatch has no persisted")
    assert job["revision_of"]["from_turn"] == "best" and job["revision_of"]["extra_turns"] == cr.DEFAULT_EXTRA_TURNS
    # The job parses with the revision runner's own reader: it is a real request, not a note.
    assert nightly_revisions._request(job) is not None
    draft = json.loads((_run(repo) / cr.RETRY_DRAFT_FILE).read_text())
    assert draft["schema"] == cr.RETRY_DRAFT_SCHEMA and [j["job_id"] for j in draft["jobs"]] == [job["job_id"]]
    assert not (repo / "runs" / cr.REVISION_QUEUE).exists()
    # Retrying again with a note replaces the draft job; the note leads, the recorded failure follows.
    again = client.post(f"/api/review/{BLOCKED_RUN}/retry/{CADAM}", json={"note": "Credits are topped up."}, headers=ORIGIN).json()
    assert again["job"]["revision_of"]["feedback"] == f"Credits are topped up.\n\n({job['revision_of']['feedback']})"
    assert len(json.loads((_run(repo) / cr.RETRY_DRAFT_FILE).read_text())["jobs"]) == 1


def test_retry_writes_the_revision_queue_when_enabled(repo: Path):
    client = _client(repo, control_room_queue_writes=True)
    queue = repo / "runs" / cr.REVISION_QUEUE
    first = client.post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={"note": "Use arc.Curve.Center."}, headers=ORIGIN).json()
    assert first["queued"] is True and first["queue"] == {"added": 1, "replaced": 0, "skipped": 0} and first["draft"] is None
    jobs = json.loads(queue.read_text())["jobs"]
    assert [j["job_id"] for j in jobs] == ["cr-20261018T185726Z-sambuca-retry-freecad-opus"]
    assert jobs[0]["revision_of"]["trial_id"] == FREECAD and jobs[0]["revision_of"]["feedback"].startswith("Use arc.Curve.Center.")
    assert not (_run(repo) / cr.RETRY_DRAFT_FILE).exists()
    # A second retry of the same entrant replaces the still-queued job; other jobs are untouched.
    client.post(f"/api/review/{BLOCKED_RUN}/retry/{SOLIDWORKS}", json={}, headers=ORIGIN)
    second = client.post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={"note": "Second note."}, headers=ORIGIN).json()
    assert second["queue"] == {"added": 0, "replaced": 1, "skipped": 0}
    jobs = json.loads(queue.read_text())["jobs"]
    assert sorted(j["job_id"] for j in jobs) == sorted(["cr-20261018T185726Z-sambuca-retry-freecad-opus",
                                                        "cr-20261018T185726Z-sambuca-retry-solidworks-sol"])
    assert next(j for j in jobs if j["job_id"].endswith("freecad-opus"))["revision_of"]["feedback"].startswith("Second note.")
    # A job a runner already took is never rewritten, and the retry says so.
    assert [j["job_id"].rsplit("-retry-", 1)[1] for j in jobs] == ["freecad-opus", "solidworks-sol"]  # replaced in place
    payload = json.loads(queue.read_text())
    freecad = next(j for j in payload["jobs"] if j["job_id"].endswith("freecad-opus"))
    freecad["status"] = "running"
    queue.write_text(json.dumps(payload))
    taken = client.post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={"note": "Third."}, headers=ORIGIN).json()
    assert taken["queued"] is False and taken["queue"] == {"added": 0, "replaced": 0, "skipped": 1}
    jobs = json.loads(queue.read_text())["jobs"]
    assert len(jobs) == 2
    running = next(j for j in jobs if j["job_id"].endswith("freecad-opus"))
    assert running["status"] == "running" and running["revision_of"]["feedback"].startswith("Second note.")
    # The nightly queue is never written from here.
    assert not (repo / "runs" / "nightly-cad-queue.json").exists()


def test_retry_validates_its_payload(repo: Path):
    client = _client(repo)
    assert client.post(f"/api/review/{BLOCKED_RUN}/retry/nope", json={}, headers=ORIGIN).status_code == 400
    assert client.post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={"note": "x" * (cr.MAX_NOTE_CHARS + 1)}, headers=ORIGIN).status_code == 400
    assert client.post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={"extra_turns": 0}, headers=ORIGIN).status_code == 400
    assert client.post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={"extra_turns": cr.MAX_EXTRA_TURNS + 1}, headers=ORIGIN).status_code == 400
    assert not (_run(repo) / cr.RETRY_DRAFT_FILE).exists()


def test_retry_refuses_a_symlinked_control_room_folder(repo: Path, tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (repo / "runs" / "control-room").symlink_to(outside, target_is_directory=True)
    response = _client(repo, control_room_queue_writes=True).post(f"/api/review/{BLOCKED_RUN}/retry/{FREECAD}", json={}, headers=ORIGIN)
    assert response.status_code == 409 and "symlink" in response.text and list(outside.iterdir()) == []


def test_blocked_night_review_still_saves_and_ranks_the_placeholder(repo: Path):
    client = _client(repo)
    order = client.get(f"/api/review/{BLOCKED_RUN}").json()["grader_order"]
    saved = client.post(f"/api/review/{BLOCKED_RUN}", json={"ranking": order}, headers=ORIGIN).json()["review"]
    assert saved["ranking"] == order and saved["revision_requests"] == []


def test_api_payload_and_no_js_page_have_no_host_paths(repo: Path):
    client = _client(repo)
    body = client.get(f"/api/review/{BLOCKED_RUN}").text
    assert str(repo) not in body
    page = client.get(f"/review/{BLOCKED_RUN}").text
    assert str(repo) not in page
    assert "This night is <strong>blocked</strong>." in page and "Add OpenRouter credits" in page
    assert "build_transcript.txt" in page and "cadam-stream.bin (decoded)" in page
    assert page.count("<li><strong>") == 4
