"""New Run's Fill the gaps preset (#1110)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from test_control_room_library import PAGE  # noqa: E402
from test_control_room_newrun import build_newrun_repo  # noqa: E402
from test_control_room_variants import build_instruments_root  # noqa: E402

from makerbench import nightly_cad  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}
LATER = (date.today() + timedelta(days=3)).isoformat()


def link_assembly_to_cajon(repo: Path) -> None:
    """Point the fixture's wood-assembly task at the library's CAD-less cajon card: a gap the runner can't apply."""
    registry = json.loads((repo / "registry.json").read_text(encoding="utf-8"))
    for task in registry["instruments"]:
        if task["id"] == "tongue_drum_wood_assembly":
            task["repo_path"] = "percussion/cajon"
    (repo / "registry.json").write_text(json.dumps(registry), encoding="utf-8")


def _client(tmp_path: Path, refresh: bool = True, mixed: bool = False, **kwargs) -> tuple[TestClient, Path]:
    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    if mixed:
        link_assembly_to_cajon(repo)
    root = build_instruments_root(tmp_path / "instruments")
    app = create_studio_app(registry_path=repo / "registry.json", repo_root=repo, instruments_root=root,
                            library_fetch=lambda: PAGE, **kwargs)
    client = TestClient(app, base_url="http://127.0.0.1")
    if refresh:
        client.post("/api/newrun/library/refresh", headers=ORIGIN)
    return client, repo


def test_gap_summary_counts_explorers_and_instruments_without_cad(tmp_path: Path):
    client, _ = _client(tmp_path)
    gaps = client.get("/api/newrun/catalog").json()["gaps"]
    assert gaps["explorers_complete"] == 0 and gaps["total"] == 5
    assert gaps["without_cad"] == ["kora", "cajon", "bansuri", "glass-harp"]


def test_fill_gaps_needs_the_library(tmp_path: Path):
    client, _ = _client(tmp_path, refresh=False)
    response = client.post("/api/newrun/fill-gaps", json={"instruments": ["kora"]}, headers=ORIGIN)
    assert response.status_code == 400 and "Refresh from the library" in response.text


def test_one_instrument_per_night_in_order_with_skips_reported(tmp_path: Path):
    client, repo = _client(tmp_path)
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    body = {"instruments": ["cajon", "kora", "handpan", "kora", "nope"]}
    result = client.post("/api/newrun/fill-gaps", json=body, headers=ORIGIN).json()
    assert result["queued"] is False and queue.read_bytes() == before
    assert result["order"] == ["kora"] and result["first_night"] == date.today().isoformat()
    # Every instrument left out says why: CAD already, not in the library, no reference image.
    assert {s["instrument"]: s["reason"] for s in result["skipped"]}.keys() == {"cajon", "handpan", "nope"}
    assert {s["instrument"]: s["reason"] for s in result["skipped"]}["handpan"] == "already has CAD"
    job = result["jobs"][0]
    assert job["instrument_id"] == "kora"
    assert [e["entrant_id"] for e in job["entrants"]] == ["cadam-fable", "freecad-opus", "solidworks-sol", "fusion-sonnet"]
    plan = json.loads((repo / "runs" / result["plans"][0]).read_text())
    assert plan["preset"] == "fill-gaps" and plan["order"] == 1 and plan["first_night"] == date.today().isoformat()
    assert plan["brief"].startswith("Calabash harp.")  # registry brief plus repo facts
    # duplicates collapse.
    assert len(result["jobs"]) == 1


def test_fill_gaps_with_queue_writes_appends_in_order(tmp_path: Path):
    client, repo = _client(tmp_path, control_room_queue_writes=True)
    result = client.post("/api/newrun/fill-gaps", json={"instruments": ["kora"]}, headers=ORIGIN).json()
    assert result["queued"] is True and result["added"] == 1 and result["ahead"] == 1  # the fixture's job runs first
    _payload, jobs = nightly_cad.load_queue(repo / "runs" / "nightly-cad-queue.json")
    assert [job.instrument_id for job in jobs] == ["kora", "kora"]  # the fixture's existing job, then the new night
    assert client.post("/api/newrun/fill-gaps", json={"instruments": ["cajon"]}, headers=ORIGIN).status_code == 400


def test_a_later_first_night_is_staged_never_queued(tmp_path: Path):
    client, repo = _client(tmp_path, control_room_queue_writes=True)
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    result = client.post("/api/newrun/fill-gaps", json={"instruments": ["kora"], "start_date": LATER}, headers=ORIGIN).json()
    assert result["queued"] is False and queue.read_bytes() == before  # the runner would take it tonight
    assert any("no start date" in gap for gap in result["staged_because"]) and result["draft"]
    plan = json.loads((repo / "runs" / result["plans"][0]).read_text())
    assert plan["first_night"] == LATER and any("no start date" in gap for gap in plan["runner_cannot_apply"])
    past = (date.today() - timedelta(days=1)).isoformat()
    response = client.post("/api/newrun/fill-gaps", json={"instruments": ["kora"], "start_date": past}, headers=ORIGIN)
    assert response.status_code == 400 and "past" in response.text


def test_more_than_thirty_nights_is_refused_not_truncated(tmp_path: Path):
    from makerbench.arena_studio import control_room_newrun as nr

    client, repo = _client(tmp_path, control_room_queue_writes=True)
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    picked = ["kora", *[f"gap-{i}" for i in range(nr.MAX_GAP_NIGHTS)]]
    response = client.post("/api/newrun/fill-gaps", json={"instruments": picked}, headers=ORIGIN)
    assert response.status_code == 400 and "at most 30 nights" in response.text and "31 were picked" in response.text
    assert queue.read_bytes() == before and not (repo / "runs" / "control-room" / "plans").exists()
    forms, skipped = nr.fill_gap_forms(picked[:nr.MAX_GAP_NIGHTS], {"kora": {"id": "kora", "cad": "none"}},
                                       brief_for=lambda card: "", hero_for=lambda card: None)
    assert len(forms) + len(skipped) == nr.MAX_GAP_NIGHTS  # every picked instrument is accounted for


def test_a_mixed_batch_reports_queued_and_staged_apart(tmp_path: Path):
    client, repo = _client(tmp_path, mixed=True, control_room_queue_writes=True)
    body = {"instruments": ["kora", "tongue_drum_wood_assembly"]}
    result = client.post("/api/newrun/fill-gaps", json=body, headers=ORIGIN).json()
    assert result["queued"] is True and result["added"] == 1
    assert result["order"] == ["kora", "tongue_drum_wood_assembly"]  # the planned order
    assert result["queued_order"] == ["kora"]  # what is actually in the queue
    assert result["staged"] == [{"instrument": "tongue_drum_wood_assembly", "reasons": ["the wood-assembly mode"]}]
    assert result["draft"] and (repo / "runs" / result["draft"]).is_file()
    _payload, jobs = nightly_cad.load_queue(repo / "runs" / "nightly-cad-queue.json")
    assert [job.instrument_id for job in jobs] == ["kora", "kora"]  # the staged assembly never reached the queue
