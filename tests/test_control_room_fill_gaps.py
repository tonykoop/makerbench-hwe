"""New Run's Fill the gaps preset (#1110)."""

from __future__ import annotations

import json
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


def _client(tmp_path: Path, refresh: bool = True, **kwargs) -> tuple[TestClient, Path]:
    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
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
    body = {"instruments": ["cajon", "kora", "handpan", "kora"], "start_date": "2026-10-20"}
    result = client.post("/api/newrun/fill-gaps", json=body, headers=ORIGIN).json()
    assert result["queued"] is False and queue.read_bytes() == before
    assert result["nights"] == ["2026-10-20"]  # kora, the only one that can be queued
    assert [s["instrument"] for s in result["skipped"]] == ["cajon"]  # no repo, no reference image
    job = result["jobs"][0]
    assert job["instrument_id"] == "kora"
    assert [e["entrant_id"] for e in job["entrants"]] == ["cadam-fable", "freecad-opus", "solidworks-sol", "fusion-sonnet"]
    plan = json.loads((repo / "runs" / result["plans"][0]).read_text())
    assert plan["preset"] == "fill-gaps" and plan["night"] == "2026-10-20"
    assert plan["brief"].startswith("Calabash harp.")  # registry brief plus repo facts
    # handpan already has CAD and is never queued; duplicates collapse.
    assert len(result["jobs"]) == 1


def test_fill_gaps_with_queue_writes_appends_in_order(tmp_path: Path):
    client, repo = _client(tmp_path, control_room_queue_writes=True)
    result = client.post("/api/newrun/fill-gaps", json={"instruments": ["kora"], "start_date": "2026-10-20"}, headers=ORIGIN).json()
    assert result["queued"] is True and result["added"] == 1
    _payload, jobs = nightly_cad.load_queue(repo / "runs" / "nightly-cad-queue.json")
    assert [job.instrument_id for job in jobs] == ["kora", "kora"]  # the fixture's existing job, then the new night
    assert client.post("/api/newrun/fill-gaps", json={"instruments": ["cajon"]}, headers=ORIGIN).status_code == 400
