"""Every data section has a deterministic, current no-JS fallback (#837)."""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("homepage_builder", ROOT / "site/build_data.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_all_sections_are_prerendered_from_current_committed_inputs():
    page = (ROOT / "site/index.html").read_text()
    payload = builder.build_payload(ROOT / "results", ROOT / "tasks/registry.json")
    assert "Loading the landscape" not in page
    assert "Loading citation" not in page
    for name in ("charts", "tasks", "extended", "delta-dossier", "arena", "ecosystem",
                 "findings", "landscape", "roadmap-status", "roadmap-packs", "roadmap-phases",
                 "roadmap-horizon", "roadmap-docs", "citation-bibtex", "citation-apa"):
        assert f"<!-- prerender:{name} -->" in page, name
    assert f"results as of {payload['data_updated'][:10]}" in page
    assert '<section id="arena" hidden' not in page
    assert '<section id="findings" hidden' not in page
    assert builder.inject_prerendered(page, payload) == page


def test_stale_committed_content_is_replaced_and_detectable():
    page = (ROOT / "site/index.html").read_text()
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    stale = page.replace(payload["citation"]["bibtex"].splitlines()[0], "STALE citation", 1)
    assert stale != page
    assert builder.inject_prerendered(stale, payload) == page


def test_no_result_sample_is_invented_for_empty_diagnostic_sections():
    page = (ROOT / "site/index.html").read_text()
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    if not payload["extended_families"]:
        assert "No extended-family data collected yet." in page
    if not payload["delta_dossier"]["stacks"]:
        assert "No comparable repeated stack observations" in page


def test_refresh_keeps_matchups_and_all_extended_sections():
    page = (ROOT / "site/index.html").read_text()
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    matchups = json.loads((ROOT / "site/data/matchups.json").read_text())
    blocks = builder.prerender_blocks(payload, matchups=matchups)
    assert len(matchups["matchups"]) == 66
    assert blocks["matchups"] == builder._prerender_matchups_html(matchups)
    assert all(blocks[key] for key in ("freshness", "charts", "tasks", "ecosystem"))
    assert builder.inject_prerendered(page, payload, matchups=matchups) == page
    assert builder.inject_prerendered(page, payload) == page
