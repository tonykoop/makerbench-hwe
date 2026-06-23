"""Acceptance locks for landing-page findings teasers (#172)."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"

EXPECTED_KEYS = {
    "hardest-tier-zero",
    "welded-assembly",
    "bom-omission",
    "perception-lift",
    "token-cost-gap",
}


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if "id" in attr:
            self.ids.add(attr["id"])


def test_issue_172_homepage_mounts_blog_derived_findings_section():
    page = _Ids()
    page.feed((SITE / "index.html").read_text(encoding="utf-8"))
    blog = _Ids()
    blog.feed((SITE / "blog" / "index.html").read_text(encoding="utf-8"))

    assert {"findings", "findings-eyebrow", "findings-title", "findings-lede", "findings-grid"} <= page.ids
    assert "mb-findings" in blog.ids
    app_js = (SITE / "assets" / "app.js").read_text(encoding="utf-8")
    assert 'fetch("data/findings.json"' in app_js
    assert "function renderFindings()" in app_js


def test_issue_172_committed_findings_cover_headlines_and_resolve_posts():
    payload = json.loads((SITE / "data" / "findings.json").read_text())
    findings = payload["findings"]
    by_key = {finding["key"]: finding for finding in findings}

    assert set(by_key) == EXPECTED_KEYS
    assert payload["section"]["title"] == "What we've learned"
    assert "doesn't just rank" in payload["section"]["lede"]

    for finding in findings:
        assert finding["headline"]
        assert finding["detail"]
        assert finding["href"].startswith("blog/")
        assert (SITE / finding["href"].split("#", 1)[0]).is_file()

    assert "0%" in by_key["hardest-tier-zero"]["headline"]
    assert "welded" in by_key["welded-assembly"]["headline"].lower()
    assert "BOM" in by_key["bom-omission"]["headline"]
    assert "Perception" in by_key["perception-lift"]["headline"]
    assert "budgets" in by_key["token-cost-gap"]["headline"]


def test_issue_172_failure_gallery_thumbnails_resolve_to_public_assets():
    payload = json.loads((SITE / "data" / "findings.json").read_text())
    gallery = json.loads((SITE / "data" / "failure_gallery.json").read_text())
    gallery_ids = {entry["id"]: entry for entry in gallery["examples"]}

    thumbed = [finding for finding in payload["findings"] if "thumb" in finding]
    assert thumbed, "at least one finding should carry a failure-gallery thumbnail"

    for finding in thumbed:
        thumb = finding["thumb"]
        assert thumb["gallery_id"] in gallery_ids
        assert thumb["alt"]
        assert thumb["src"].startswith("assets/failure-gallery/")
        assert (SITE / thumb["src"]).is_file()
        entry = gallery_ids[thumb["gallery_id"]]
        assert entry["privacy"]["uses_oracle"] is False
        assert entry["privacy"]["reviewed"] is True
