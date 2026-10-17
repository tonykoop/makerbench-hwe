"""Control Room Morning Review: reader, Save validation and routes (#1105)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("trimesh")

from fastapi.testclient import TestClient  # noqa: E402

from control_room_fixture import DRUM_RUN, HOSTILE, SAMBUCA_RUN, build_control_room_repo  # noqa: E402

from makerbench.arena_studio import control_room as cr  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}
SOLIDWORKS = "sambuca__seed0__rep0__solidworks-sol"
CADAM = "sambuca__seed0__rep0__cadam-fable"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = build_control_room_repo(tmp_path)
    (repo / "runs" / "nightly-cad-queue.json").write_text(json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": []}))
    return repo


def _client(repo: Path, **kwargs) -> TestClient:
    return TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, **kwargs), base_url="http://127.0.0.1")


def _night(repo: Path, run: str = SAMBUCA_RUN) -> Path:
    return repo / "runs" / "nightly" / run


def test_nights_are_found_newest_first_and_private_folders_skipped(repo: Path):
    private = repo / "runs" / ".makerbench-private" / "x"
    workbench = repo / "runs" / "workbench" / "y"
    for planted in (private, workbench):
        planted.mkdir(parents=True)
        (planted / "run_log.json").write_text("{}")
        (planted / "morning-summary.json").write_text("{}")
    assert [path.name for path in cr.discover_nights(repo / "runs")] == [SAMBUCA_RUN, DRUM_RUN]


def test_view_reads_trials_through_the_one_reader(repo: Path):
    run = _night(repo)
    log = json.loads((run / "run_log.json").read_text())
    # An off-track (external STEP) row never becomes a card.
    log["trials"].append({"trial_id": "step-1", "model_id": "ext", "entrant_type": "external_step", "status": "scored"})
    (run / "run_log.json").write_text(json.dumps(log))
    view = cr.review_view(run, repo)
    assert [d["trial_id"] for d in view["designs"]] == [d["trial_id"] for d in view["designs"] if d["trial_id"] != "step-1"]
    assert len(view["designs"]) == 4
    solid = next(d for d in view["designs"] if d["trial_id"] == SOLIDWORKS)
    assert [c["state"] for c in solid["checks"]] == ["pass", "pass", "pass", "fail", "pass"]
    assert solid["failures"] == ["The neck measures 1412 mm; the envelope allows 1300 mm."]
    assert solid["best_turn"] == 6 and solid["native"] == {"file": "sambuca.SLDPRT", "app": "SolidWorks"}
    assert view["grader_order"][-1].endswith("fusion-sonnet")


def test_retired_checks_never_show(repo: Path):
    run = _night(repo)
    log = json.loads((run / "run_log.json").read_text())
    log["trials"][0]["result"]["objective"]["sub_scores"]["min_wall"] = 0.0
    (run / "run_log.json").write_text(json.dumps(log))
    design = cr.review_view(run, repo)["designs"][0]
    assert "min_wall" not in [c["name"] for c in design["checks"]] and design["all_pass"]


def test_recorded_paths_outside_the_run_folder_are_ignored(repo: Path, tmp_path: Path):
    outside = tmp_path / "secret.FCStd"
    outside.write_text("x")
    run = _night(repo)
    log = json.loads((run / "run_log.json").read_text())
    log["trials"][0]["result"]["native_path"] = str(outside)
    log["trials"][0]["result"]["artifacts"]["stl_path"] = "../../../secret.FCStd"
    (run / "run_log.json").write_text(json.dumps(log))
    design = cr.review_view(run, repo)["designs"][0]
    assert design["native"] is None  # never the outside file
    assert design["has_mesh"] is False


def test_save_validates_the_payload(repo: Path):
    client = _client(repo)
    view = client.get(f"/api/review/{SAMBUCA_RUN}").json()
    order = view["grader_order"]
    bad = [
        {"ranking": order[:-1]},
        {"ranking": order + [order[0]]},
        {"ranking": order, "notes": {"nope": "x"}},
        {"ranking": order, "revise": [SOLIDWORKS]},  # no note
        {"ranking": order, "extra_turns": 0},
        {"ranking": order, "notes": {CADAM: "x" * (cr.MAX_NOTE_CHARS + 1)}},
    ]
    for payload in bad:
        assert client.post(f"/api/review/{SAMBUCA_RUN}", json=payload, headers=ORIGIN).status_code in (400, 422), payload
    assert not (_night(repo) / cr.REVIEW_FILE).exists()
    # Same-origin rule applies to the new POSTs too.
    assert client.post(f"/api/review/{SAMBUCA_RUN}", json={"ranking": order}).status_code == 403


def test_queue_writes_stay_off_by_default_and_append_when_enabled(repo: Path):
    nightly = repo / "runs" / "nightly-cad-queue.json"
    revisions = repo / "runs" / cr.REVISION_QUEUE
    payload = {"ranking": [CADAM, SOLIDWORKS, "sambuca__seed0__rep0__freecad-opus", "sambuca__seed0__rep0__fusion-sonnet"],
               "notes": {SOLIDWORKS: "Shorter neck."}, "revise": [SOLIDWORKS]}
    nightly_before = nightly.read_bytes()
    saved = _client(repo).post(f"/api/review/{SAMBUCA_RUN}", json=payload, headers=ORIGIN).json()["review"]
    assert saved["queued"] is False and not revisions.exists()
    assert saved["next_night_jobs"][0]["revision_of"] == {
        "run_id": SAMBUCA_RUN, "trial_id": SOLIDWORKS, "from_turn": 6, "extra_turns": 3, "feedback": "Shorter neck."}

    saved = _client(repo, control_room_queue_writes=True).post(f"/api/review/{SAMBUCA_RUN}", json=payload, headers=ORIGIN).json()["review"]
    assert saved["queued"] is True
    queue = json.loads(revisions.read_text())
    assert queue["schema"] == cr.REVISION_QUEUE_SCHEMA
    assert [job["job_id"] for job in queue["jobs"]] == ["sambuca-photo-revise-solidworks-sol"]
    # Saving again does not queue the same job twice, and the nightly queue is never written.
    _client(repo, control_room_queue_writes=True).post(f"/api/review/{SAMBUCA_RUN}", json=payload, headers=ORIGIN)
    assert len(json.loads(revisions.read_text())["jobs"]) == 1
    assert nightly.read_bytes() == nightly_before


def test_open_uses_only_the_recorded_native_file(repo: Path):
    opened: list[Path] = []
    client = _client(repo, native_opener=opened.append)
    assert client.post(f"/api/review/{SAMBUCA_RUN}/open/{SOLIDWORKS}", headers=ORIGIN).json() == {"opened": "sambuca.SLDPRT", "app": "SolidWorks"}
    assert opened == [_night(repo) / "gen" / SOLIDWORKS / "sambuca.SLDPRT"]
    assert client.post(f"/api/review/{SAMBUCA_RUN}/open/nope", headers=ORIGIN).status_code == 404
    assert client.post(f"/api/review/..%2F..%2Fetc/open/{SOLIDWORKS}", headers=ORIGIN).status_code == 404

    def broken(_path):
        raise OSError("no Windows here")

    assert _client(repo, native_opener=broken).post(f"/api/review/{SAMBUCA_RUN}/open/{SOLIDWORKS}", headers=ORIGIN).status_code == 409


def test_files_routes_serve_only_this_nights_artifacts(repo: Path):
    client = _client(repo)
    assert client.get(f"/api/review/{SAMBUCA_RUN}/mesh/{CADAM}").headers["content-type"] == "model/gltf-binary"
    assert client.get(f"/api/review/{SAMBUCA_RUN}/render/{CADAM}").headers["content-type"] == "image/png"
    assert client.get(f"/api/review/{SAMBUCA_RUN}/reference").status_code == 200
    assert client.get(f"/api/review/{SAMBUCA_RUN}/native/{CADAM}").content == b"synthetic sambuca.scad\n"
    assert client.get(f"/api/review/{DRUM_RUN}/mesh/{CADAM}").status_code == 404
    assert client.get("/api/review/not-a-night").status_code == 404


def test_api_payload_has_no_host_paths(repo: Path):
    body = _client(repo).get(f"/api/review/{SAMBUCA_RUN}").text
    assert str(repo) not in body


def test_no_js_pages_escape_run_text(repo: Path):
    client = _client(repo)
    index = client.get("/review").text
    assert "Sambuca from the museum photo" in index and "Tongue drum v2" in index
    night = client.get(f"/review/{SAMBUCA_RUN}").text
    assert HOSTILE not in night and "&lt;img src=x" in night
    assert night.count("<li><strong>") == 4


def test_assembly_night_lists_parts_and_cut_list(repo: Path):
    view = cr.review_view(_night(repo, DRUM_RUN), repo)
    assert view["assembly"] is True
    freecad = view["designs"][0]
    assert freecad["assembly"]["parts"] == ["top", "bottom", "front", "back", "left", "right"]
    assert freecad["assembly"]["cut_list"][0]["material"] == "padauk"
    cadam = view["designs"][1]
    assert cadam["failures"] == ["The mesh has 1 separate body; the brief needs 6."]
