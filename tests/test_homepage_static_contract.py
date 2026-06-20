"""Static homepage contract tests for the front-page overhaul."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HomepageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.links: set[str] = set()
        self._capture_heading: str | None = None
        self._buf: list[str] = []
        self.headings: list[str] = []
        self.dl_attrs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if "id" in attr:
            self.ids.add(attr["id"])
        for cls in attr.get("class", "").split():
            self.classes.add(cls)
        if tag == "a" and "href" in attr:
            self.links.add(attr["href"])
        if tag in {"h1", "h2", "h3"}:
            self._capture_heading = tag
            self._buf = []
        if tag == "dl":
            self.dl_attrs.append(attr)

    def handle_data(self, data: str) -> None:
        if self._capture_heading:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._capture_heading:
            text = " ".join("".join(self._buf).split())
            if text:
                self.headings.append(text)
            self._capture_heading = None


def _parse_homepage() -> HomepageParser:
    parser = HomepageParser()
    parser.feed((ROOT / "site" / "index.html").read_text(encoding="utf-8"))
    return parser


def test_homepage_hero_exposes_front_door_and_data_stat_hook():
    parsed = _parse_homepage()

    assert "Can a model reason about hardware design?" in parsed.headings
    assert {"headline", "hero-stats", "why", "leaderboard", "get-started"}.issubset(
        parsed.ids
    )
    assert {"#leaderboard", "#why", "#get-started"}.issubset(parsed.links)
    assert {"hero", "hero-cta", "stat-strip"}.issubset(parsed.classes)
    assert any(attrs.get("id") == "hero-stats" and "hidden" in attrs for attrs in parsed.dl_attrs)


def test_homepage_why_section_carries_all_issue_168_value_props():
    parsed = _parse_homepage()

    expected_cards = {
        "Parametric task generation",
        "Deterministic geometric graders",
        "Two tracks, two numbers",
        "A real parts library",
        "Four failure levels",
        "Design-dossier contract",
        "Open, CI-runnable stack",
    }

    assert "Why MakerBench is different" in parsed.headings
    assert expected_cards.issubset(set(parsed.headings))
    assert "why-grid" in parsed.classes
    assert "why-card" in parsed.classes


def test_homepage_stats_are_rendered_from_leaderboard_payload():
    app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")

    assert "function renderHeroStats()" in app_js
    assert "var hs = DATA.hero_stats;" in app_js
    assert 'document.getElementById("headline").textContent = data.headline || "";' in app_js
    assert "el.hidden = false;" in app_js
