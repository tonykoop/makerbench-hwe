"""New Run variants come from the instrument repo (#1109)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from test_control_room_newrun import build_newrun_repo  # noqa: E402

from makerbench.arena_studio import create_studio_app  # noqa: E402
from makerbench.arena_studio import instrument_repo as ir  # noqa: E402


def _png() -> bytes:
    from test_control_room_newrun import PNG

    return PNG


def build_instruments_root(root: Path) -> Path:
    """Synthetic instrument repos: a family-spec.csv, a variants.csv, a private tree and a symlink."""
    kora = root / "strings" / "kora"
    kora.mkdir(parents=True)
    (kora / "family-spec.csv").write_text(
        "member_id,role,status,string_count,unit_price_usd,customer_name\n"
        "KORA-21-ROOT,root prototype,private review,21,900,Jane Doe\n"
        "KORA-13-STUDY,bridge study,planned,13,400,Someone\n",
        encoding="utf-8",
    )
    drum = root / "idiophones" / "tongue-drum"
    drum.mkdir(parents=True)
    (drum / "family-spec.csv").write_text("member_id,role\nOLD-1,old\n", encoding="utf-8")
    (drum / "variants.csv").write_text(
        "variant_id,tuning,root_note,tongues,material\nTD-8,D minor,D3,8,padauk\nTD-10,akebono,F3,10,walnut\nTD-12,C major,C3,12,maple\n",
        encoding="utf-8",
    )
    (kora / "capstone-manifest.json").write_text(
        '{"instrument": "Kora-inspired 21-string prototype", "engineering": {"bowl_diameter_in": 20.3, '
        '"neck_length_in": 51.2, "supplier_price_usd": 120, "cloud_url": "https://x"}}', encoding="utf-8")
    (kora / "images").mkdir()
    (kora / "images" / "hero-render.png").write_bytes(_png())
    (kora / "images" / "family-group.png").write_bytes(_png())
    (kora / "images" / "notes.txt").write_text("not an image", encoding="utf-8")
    (kora / "data").mkdir()
    (kora / "data" / "string-schedule.csv").write_text("string,note,length_in\n1,F2,31.1\n2,C3,29.0\n", encoding="utf-8")
    (kora / "bom.csv").write_text("part,unit_price_usd\ncalabash,80\n", encoding="utf-8")
    (kora / "tuning-price-list.csv").write_text("note,price\nF2,10\n", encoding="utf-8")
    flute = root / "woodwind" / "bansuri"
    (flute / "data").mkdir(parents=True)
    (flute / "family-spec.csv").write_text("member_id,role,bore_mm\nBANSURI-A,root,22\n", encoding="utf-8")
    (flute / "data" / "hole-table.csv").write_text("hole,position_mm\n1,210\n", encoding="utf-8")
    secret = root / "private" / "sambuca"
    secret.mkdir(parents=True)
    (secret / "variants.csv").write_text("variant_id\nSECRET\n", encoding="utf-8")
    try:
        os.symlink(secret, root / "strings" / "sambuca")
    except OSError:  # pragma: no cover - filesystems without symlinks
        pass
    return root


def test_variants_read_variants_csv_first_and_drop_private_columns(tmp_path: Path):
    root = build_instruments_root(tmp_path / "instruments")
    drum = ir.variants(ir.find_repo(root, "tongue-drum"))
    assert drum["source"] == "variants.csv"
    assert [v["id"] for v in drum["variants"]] == ["TD-8", "TD-10", "TD-12"]
    assert drum["variants"][0]["label"] == "D minor"
    kora = ir.variants(ir.find_repo(root, "kora", "strings/kora"))
    assert kora["source"] == "family-spec.csv" and kora["variants"][0]["label"] == "root prototype"
    flat = repr(kora)
    assert "900" not in flat and "Jane Doe" not in flat and "price" not in flat


@pytest.mark.parametrize("slug, repo_path", [
    ("sambuca", None),               # a symlink into private/
    ("sambuca", "private/sambuca"),  # a private repo_path
    ("kora", "../strings/kora"),     # traversal in repo_path, and the slug lookup still works
])
def test_repo_lookup_is_contained(tmp_path: Path, slug, repo_path):
    root = build_instruments_root(tmp_path / "instruments")
    found = ir.find_repo(root, slug, repo_path)
    if slug == "kora":
        assert found == (root / "strings" / "kora").resolve()
    else:
        assert found is None


def test_variants_route(tmp_path: Path):
    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    root = build_instruments_root(tmp_path / "instruments")
    client = TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, instruments_root=root),
                        base_url="http://127.0.0.1")
    # The fixture registry has no repo_path for kora, so the slug finds strings/kora.
    data = client.get("/api/newrun/instrument/kora/variants").json()
    assert data["source"] == "family-spec.csv" and data["repo_found"] is True and len(data["variants"]) == 2
    assert str(root) not in client.get("/api/newrun/instrument/kora/variants").text
    assert client.get("/api/newrun/instrument/sambuca/variants").json() == {
        "source": None, "variants": [], "repo_found": False, "instruments_root": True}
    assert client.get("/api/newrun/instrument/nope/variants").status_code == 404
    bare = TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo), base_url="http://127.0.0.1")
    assert bare.get("/api/newrun/instrument/kora/variants").json()["instruments_root"] is False


def test_only_allowlisted_columns_reach_a_response(tmp_path: Path):
    """Codex #1121 probe: contact and retail columns must never become chip details."""
    root = tmp_path / "instruments"
    repo = root / "strings" / "kora"
    repo.mkdir(parents=True)
    (repo / "variants.csv").write_text(
        "variant_id,label,contact,retail_usd,Customer Name,unit_price_usd,shipping_address,string_count\n"
        "KORA-1,Study,SYNTHETIC_CUSTOMER,123.45,Jane Doe,900,1 Main St,21\n",
        encoding="utf-8",
    )
    out = ir.variants(ir.find_repo(root, "kora"))
    assert out["variants"] == [{"id": "KORA-1", "label": "Study", "status": "", "detail": "string count 21"}]
    flat = repr(out)
    for leaked in ("SYNTHETIC_CUSTOMER", "123.45", "Jane Doe", "900", "Main St", "contact", "retail"):
        assert leaked not in flat, leaked
    assert ir.public_column("bowl_diameter_in") and not ir.public_column("retail_usd")


def test_an_oversized_csv_field_is_a_clean_refusal(tmp_path: Path):
    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    root = tmp_path / "instruments"
    kora = root / "strings" / "kora"
    kora.mkdir(parents=True)
    (kora / "variants.csv").write_text("variant_id,label\nKORA-1," + "x" * 140_000 + "\n", encoding="utf-8")
    client = TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, instruments_root=root),
                        base_url="http://127.0.0.1")
    response = client.get("/api/newrun/instrument/kora/variants")
    assert response.status_code == 200
    data = response.json()
    assert data["variants"] == [] and data["source"] == "variants.csv" and "could not be read" in data["error"]


@pytest.mark.parametrize("csv_present", [True, False])
def test_variants_never_open_spreadsheets(tmp_path: Path, monkeypatch, csv_present: bool):
    """#1121 ruling: a workbook beside the CSV (or instead of it) is never opened."""
    root = tmp_path / "instruments"
    repo = root / "woodwind" / "flutes"
    repo.mkdir(parents=True)
    for name in ("Flutes.xlsx", "variants.xlsx", "family-spec.xls", "design-table.xlsx"):
        (repo / name).write_bytes(b"PK\x03\x04 synthetic workbook")
    if csv_present:
        (repo / "variants.csv").write_text("variant_id,root_note\nNAF-A4,A4\n", encoding="utf-8")
    opened: list[str] = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    out = ir.variants(ir.find_repo(root, "flutes"))
    assert not [name for name in opened if name.lower().endswith((".xlsx", ".xls"))]
    if csv_present:
        assert out["source"] == "variants.csv" and [v["id"] for v in out["variants"]] == ["NAF-A4"]
        assert "variants.csv" in opened  # the spy does see the reader's opens
    else:
        assert out == {"source": None, "variants": []}

def test_brief_fields_name_their_source_and_skip_private_files(tmp_path: Path):
    root = build_instruments_root(tmp_path / "instruments")
    out = ir.brief(ir.find_repo(root, "kora"), name="Kora", slug="kora", variant="KORA-13-STUDY")
    sources = {f["label"]: f["source"] for f in out["fields"]}
    assert sources["string count"] == "family-spec.csv › KORA-13-STUDY"
    assert sources["bowl diameter in"] == "capstone-manifest.json › engineering.bowl_diameter_in"
    assert [t["source"] for t in out["tables"]] == ["data/string-schedule.csv"]
    assert out["images"] == ["hero-render.png", "family-group.png"]
    flat = repr(out)
    assert "price" not in flat and "calabash" not in flat and "https://x" not in flat and "Jane Doe" not in flat
    assert out["text"].startswith("Model the Kora-inspired 21-string prototype, variant KORA-13-STUDY.")
    assert out["sources"] == ["capstone-manifest.json", "data/string-schedule.csv", "family-spec.csv"]


def test_flute_repos_read_no_dimensions_or_tables(tmp_path: Path):
    root = build_instruments_root(tmp_path / "instruments")
    repo = ir.find_repo(root, "bansuri")
    out = ir.brief(repo, name="Bansuri", slug="bansuri")
    assert out["fields"] == [] and out["tables"] == [] and "manager-cleaned" in out["notes"][0]
    assert ir.variants(repo, flute=True)["variants"] == [{"id": "BANSURI-A", "label": "root", "status": "", "detail": ""}]


def test_brief_and_image_routes_and_repo_references(tmp_path: Path):
    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    root = build_instruments_root(tmp_path / "instruments")
    client = TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, instruments_root=root),
                        base_url="http://127.0.0.1")
    data = client.get("/api/newrun/instrument/kora/brief").json()
    assert data["repo_found"] and data["images"] == ["hero-render.png", "family-group.png"]
    assert data["text"].startswith("Calabash harp.")  # the registry brief leads; repo facts follow
    assert client.get("/api/newrun/instrument/kora/image/hero-render.png").content == _png()
    for name in ("notes.txt", "..%2Fbom.csv", "missing.png"):
        assert client.get(f"/api/newrun/instrument/kora/image/{name}").status_code == 404
    form = {"instrument": "kora", "entrants": ["cadam:fable", "freecad:opus"], "brief": "Kora.", "turns": 4,
            "cap_usd": 1.0, "references": [{"repo_image": "family-group.png"}]}
    result = client.post("/api/newrun/tonight", json=form, headers={"Origin": "http://127.0.0.1"}).json()
    reference = repo / result["jobs"][0]["reference_image"]  # the API publishes repo-relative paths
    assert reference.read_bytes() == _png() and reference.resolve().is_relative_to((repo / "runs" / "control-room" / "plans").resolve())
    bad = client.post("/api/newrun/tonight", json={**form, "references": [{"repo_image": "notes.txt"}]},
                      headers={"Origin": "http://127.0.0.1"})
    assert bad.status_code == 400 and "not in the instrument repo" in bad.text


def test_private_manifest_sections_are_dropped_whole(tmp_path: Path):
    root = build_instruments_root(tmp_path / "instruments")
    kora = root / "strings" / "kora"
    (kora / "capstone-manifest.json").write_text(json.dumps({
        "instrument": "Kora", "string_count": 21,
        "customer": {"key": "SYNTHETIC_CUSTOMER_KEY", "length_in": 99.1},
        "price": {"range": 1234},
        "engineering": {"bowl_diameter_in": 20.3, "sourcing": {"length_in": 77.7}, "quote": {"key": "SYNTHETIC_QUOTE"}},
        "notes": {"scale": "SYNTHETIC_NOTE_SCALE"},
    }), encoding="utf-8")
    out = ir.brief(ir.find_repo(root, "kora"), name="Kora", slug="kora")
    flat = repr(out)
    for leaked in ("SYNTHETIC_CUSTOMER_KEY", "99.1", "1234", "77.7", "SYNTHETIC_QUOTE", "SYNTHETIC_NOTE_SCALE"):
        assert leaked not in flat, leaked
    sources = {f["source"] for f in out["fields"]}
    assert "capstone-manifest.json › engineering.bowl_diameter_in" in sources  # the control: public fields survive
    assert "capstone-manifest.json › string_count" in sources


def test_oversized_repo_reads_are_refused_before_allocation(tmp_path: Path, monkeypatch):
    root = build_instruments_root(tmp_path / "instruments")
    kora = root / "strings" / "kora"
    (kora / "capstone-manifest.json").write_bytes(b'{"string_count": 21, "x": "' + b"y" * ir.MAX_FILE_BYTES + b'"}')
    assert ir.brief(ir.find_repo(root, "kora"), name="Kora", slug="kora")["text"].startswith("Model the Kora")
    with pytest.raises(ir.RepoFileError):
        ir.read_bounded(kora / "capstone-manifest.json", ir.MAX_FILE_BYTES)
    for name in ("hole", "tongue", "tuning", "scale", "string", "fret", "pitch"):
        (kora / f"{name}-table.csv").write_text("note,length_in\nF2,31.1\n", encoding="utf-8")
    reads = []
    real = ir.read_csv
    monkeypatch.setattr(ir, "read_csv", lambda path: reads.append(path.name) or real(path))
    assert len(ir._tables(ir.find_repo(root, "kora"))) == ir.MAX_TABLES
    assert len(reads) == ir.MAX_TABLES  # stops reading once the budget is spent


def test_an_oversized_repo_image_is_refused_without_reading_it(tmp_path: Path, monkeypatch):
    from makerbench.arena_studio import control_room_newrun as nr

    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    root = build_instruments_root(tmp_path / "instruments")
    big = root / "strings" / "kora" / "images" / "hero-render.png"
    big.write_bytes(_png() + b"\0" * (nr.MAX_REFERENCE_BYTES + 8))
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail(f"read_bytes({self.name})"))
    client = TestClient(create_studio_app(registry_path=repo / "registry.json", repo_root=repo, instruments_root=root),
                        base_url="http://127.0.0.1")
    form = {"instrument": "kora", "entrants": ["cadam:fable", "freecad:opus"], "brief": "Kora.", "turns": 4,
            "cap_usd": 1.0, "references": [{"repo_image": "hero-render.png"}]}
    response = client.post("/api/newrun/tonight", json=form, headers={"Origin": "http://127.0.0.1"})
    assert response.status_code == 400 and "under 10 MB" in response.text
