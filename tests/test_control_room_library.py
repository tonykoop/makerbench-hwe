"""New Run's instrument picker reads the library (#1107)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from test_control_room_newrun import FORM, build_newrun_repo  # noqa: E402

from makerbench.arena_studio import control_room_newrun as nr  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402
from makerbench.arena_studio import library_manifest as lm  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}
PAGE = (Path(__file__).parent / "fixtures" / "control_room" / "library.html").read_text(encoding="utf-8")


def test_cards_parse_from_the_published_page_format():
    entries = lm.entries_from_html(PAGE)
    assert [e["slug"] for e in entries] == ["kora", "handpan", "cajon", "bansuri", "glass-harp"]
    kora = entries[0]
    assert kora == {
        "slug": "kora", "title": "Kora Studio Explorer", "family": "string", "family_label": "Strings",
        "subtype": "harp-lute", "variants": 3, "hero": "/static/missing-hero-kora.png",
        "explorer": "https://tonykoop.github.io/kora/explorer.html", "cad": "none", "completeness": "near",
        "wolfram": "live", "status": "private",
    }
    assert entries[1]["variants"] == 1 and entries[1]["cad"] == "inline-glb"
    assert entries[4]["title"] == "Glass Harp <Starter>"  # entities decoded, rendered as text later
    with pytest.raises(lm.LibraryError):
        lm.entries_from_html("<html><body>No cards</body></html>")


def test_manifest_json_maps_to_the_same_entry_shape():
    payload = {"schema": lm.MANIFEST_SCHEMA, "entries": [
        {"slug": "kora", "title": "Kora", "family": "string", "acoustic_class": "harp-lute", "family_count": 4,
         "hero_image_path": "", "explorer_path": "x", "cad": "none", "completeness_state": "near", "wolfram_state": "live",
         "status": "private"},
        {"slug": "../evil", "title": "bad slug"},
    ]}
    entries = lm.entries_from_manifest(payload)
    assert [e["slug"] for e in entries] == ["kora"] and entries[0]["variants"] == 4
    with pytest.raises(lm.LibraryError):
        lm.entries_from_manifest({"schema": "other", "entries": []})


def test_cards_link_to_arena_tasks_through_repo_path():
    registry = [{"id": "kora", "repo_path": "strings/kora", "task_brief": "Calabash harp."},
                {"id": "stave-djembe", "repo_path": "percussion/cajon", "task_brief": "Box."}]
    cards = nr.library_instruments(lm.entries_from_html(PAGE), registry)
    by_slug = {card["slug"]: card for card in cards}
    assert by_slug["kora"]["id"] == "kora" and by_slug["kora"]["brief"] == "Calabash harp."
    assert by_slug["cajon"]["id"] == "stave-djembe" and by_slug["cajon"]["arena_task"] == "stave-djembe"
    assert by_slug["bansuri"]["arena_task"] is None and by_slug["bansuri"]["id"] == "bansuri"


def test_refresh_snapshots_offline_and_the_catalog_reads_it(tmp_path: Path):
    repo = build_newrun_repo(tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return PAGE

    app = create_studio_app(registry_path=repo / "registry.json", repo_root=repo, library_fetch=fetch)
    client = TestClient(app, base_url="http://127.0.0.1")
    before = client.get("/api/newrun/catalog").json()
    assert before["library"] is None and len(before["instruments"]) == 3  # the registry fallback
    assert client.post("/api/newrun/library/refresh").status_code == 403  # same-origin rule
    result = client.post("/api/newrun/library/refresh", headers=ORIGIN).json()
    assert result["count"] == 5 and result["source"] == lm.LIBRARY_URL and calls == [1]

    after = client.get("/api/newrun/catalog").json()
    assert calls == [1]  # reading the catalog never fetches
    assert after["library"]["count"] == 5 and after["library"]["fetched_utc"] == result["fetched_utc"]
    assert after["library"]["filters"]["cad"] == ["external-glb", "gltf", "inline-glb", "none"]
    assert [i["slug"] for i in after["instruments"] if i["cad"] == "none"] == ["kora", "cajon", "bansuri", "glass-harp"]
    # A library-only instrument can be queued once it has an uploaded reference.
    response = client.post("/api/newrun/tonight", json={**FORM, "instrument": "bansuri"}, headers=ORIGIN)
    assert response.status_code == 400 and "Add a reference image" in response.text


def test_refresh_failure_keeps_the_last_snapshot(tmp_path: Path):
    repo = build_newrun_repo(tmp_path)
    snapshot = repo / "runs" / lm.SNAPSHOT_PATH
    lm.refresh(snapshot, fetch=lambda: PAGE, now=datetime(2026, 10, 1, tzinfo=timezone.utc))

    def broken():
        raise OSError("offline")

    client = TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, library_fetch=broken),
                        base_url="http://127.0.0.1")
    response = client.post("/api/newrun/library/refresh", headers=ORIGIN)
    assert response.status_code == 502 and "offline" in response.text
    assert json.loads(snapshot.read_text())["fetched_utc"] == "2026-10-01T00:00:00+00:00"


def test_manifest_file_refresh(tmp_path: Path):
    manifest = tmp_path / "library-manifest.json"
    manifest.write_text(json.dumps({"schema": lm.MANIFEST_SCHEMA, "entries": [{"slug": "kora", "title": "Kora"}]}))
    snap = lm.refresh(tmp_path / "library.json", manifest_path=manifest, fetch=lambda: pytest.fail("fetched"))
    assert snap["source"] == "manifest:library-manifest.json" and len(snap["entries"]) == 1
