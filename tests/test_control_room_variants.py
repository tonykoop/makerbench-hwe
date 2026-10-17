"""New Run variants come from the instrument repo (#1109)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from test_control_room_newrun import build_newrun_repo  # noqa: E402

from makerbench.arena_studio import create_studio_app  # noqa: E402
from makerbench.arena_studio import instrument_repo as ir  # noqa: E402


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
