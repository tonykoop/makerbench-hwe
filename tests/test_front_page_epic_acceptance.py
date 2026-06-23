"""Epic-level acceptance locks for the front-page overhaul (#176)."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.links: set[str] = set()
        self.headings: list[str] = []
        self.meta: dict[str, str] = {}
        self._capture: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if "id" in attr:
            self.ids.add(attr["id"])
        for class_name in attr.get("class", "").split():
            self.classes.add(class_name)
        if tag == "a" and "href" in attr:
            self.links.add(attr["href"])
        if tag == "meta":
            key = attr.get("name") or attr.get("property")
            if key:
                self.meta[key] = attr.get("content", "")
        if tag in {"h1", "h2", "h3"}:
            self._capture = tag
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._capture:
            text = " ".join("".join(self._buf).split())
            if text:
                self.headings.append(text)
            self._capture = None


def _parse_index() -> _PageParser:
    parser = _PageParser()
    parser.feed((SITE / "index.html").read_text(encoding="utf-8"))
    return parser


def test_issue_176_front_page_exposes_full_ecosystem_information_architecture():
    page = _parse_index()

    required_sections = {
        "why",
        "leaderboard",
        "tracks-leagues",
        "charts",
        "tasks",
        "covers",
        "delta-dossier",
        "ecosystem",
        "findings",
        "methodology",
        "roadmap",
        "landscape",
        "get-started",
        "about",
    }
    assert required_sections <= page.ids
    assert {"field-map-card", "hero-stats", "track-grid", "ecosystem-grid"}.issubset(
        page.ids
    )
    assert {
        "opportunity-matrix.html",
        "inspect.html",
        "run-library.html",
        "domains.html",
        "blog/",
        "#landscape",
        "#get-started",
    } <= page.links
    assert "Can a model reason about hardware design?" in page.headings
    assert "One ruler, four arenas" in page.headings
    assert "The MakerBench ecosystem" in page.headings
    assert "Roadmap" in " ".join(page.headings)


def test_issue_176_front_page_payloads_are_generated_and_complete():
    leaderboard = json.loads((SITE / "data" / "leaderboard.json").read_text())
    for key in (
        "hero_stats",
        "track_explainer",
        "ecosystem",
        "roadmap",
        "citation",
    ):
        assert leaderboard.get(key), key

    for rel in (
        "data/domains.json",
        "data/findings.json",
        "data/get_started.json",
        "data/landscape.json",
        "data/opportunity-matrix.json",
        "data/run-library.json",
    ):
        payload = json.loads((SITE / rel).read_text())
        assert payload, rel


def test_issue_176_front_page_keeps_public_integrity_and_preview_metadata():
    page = _parse_index()
    html = (SITE / "index.html").read_text(encoding="utf-8")

    assert page.meta["description"]
    assert page.meta["og:type"] == "website"
    assert page.meta["og:url"].startswith("https://")
    assert page.meta["og:image"].startswith("https://")
    assert page.meta["twitter:card"] == "summary_large_image"
    assert page.meta["robots"] == "index, follow, noai, noimageai"
    assert "private/oracles" not in html


def test_issue_176_front_page_renderers_use_site_build_data_payloads():
    app_js = (SITE / "assets" / "app.js").read_text(encoding="utf-8")

    for hook in (
        "renderHeroStats",
        "renderTrackExplainer",
        "renderEcosystem",
        "renderFindings",
        "renderRoadmap",
        "renderGetStarted",
    ):
        assert f"function {hook}" in app_js
    for payload_ref in (
        "DATA.hero_stats",
        "DATA.track_explainer",
        "DATA.ecosystem",
        "DATA.roadmap",
    ):
        assert payload_ref in app_js
    assert 'fetch("data/findings.json"' in app_js
    assert 'fetch("data/get_started.json"' in app_js

    landscape_js = (SITE / "assets" / "landscape.js").read_text(encoding="utf-8")
    assert "Reads data/landscape.json" in landscape_js
    assert 'document.getElementById("lscape-container")' in landscape_js
