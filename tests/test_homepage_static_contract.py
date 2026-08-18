"""Static homepage contract tests for the front-page overhaul."""

from __future__ import annotations

import json
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
        self.meta: list[dict[str, str]] = []
        self.link_rels: list[dict[str, str]] = []

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
        if tag == "meta":
            self.meta.append(attr)
        if tag == "link":
            self.link_rels.append(attr)

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
    # mb#670: the stat strip is prerendered at build time so no-JS visitors and
    # crawlers see it — it must NOT ship hidden (app.js re-renders it on load).
    assert any(
        attrs.get("id") == "hero-stats" and "hidden" not in attrs
        for attrs in parsed.dl_attrs
    )


def test_homepage_carries_prerendered_static_fallback():
    """mb#670: crawlers/no-JS visitors must see real results, not 'Loading…'.

    site/build_data.py bakes content between `<!-- prerender:NAME -->` marker
    pairs; the committed index.html is drift-guarded against leaderboard.json
    by site/check_data_drift.py, so real model names/scores must be present.
    """
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

    for name in (
        "headline",
        "hero-stats",
        "leaderboard",
        "tracks",
        "track-guardrail",
        "freshness",
    ):
        assert f"<!-- prerender:{name} -->" in html, name
        assert f"<!-- /prerender:{name} -->" in html, name

    payload = json.loads(
        (ROOT / "site" / "data" / "leaderboard.json").read_text(encoding="utf-8")
    )
    if payload.get("models"):
        assert "Loading current results…" not in html
        assert 'class="model-name"' in html  # static top-N leaderboard rows
        assert 'class="stat-val"' in html  # hero stat strip values
        assert 'class="track-card"' in html  # track explainer cards


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


# --------------------------------------------------------------------------- #
# #175: roadmap & status section + SEO/OG meta + about / cite
# --------------------------------------------------------------------------- #

def test_seo_og_twitter_and_canonical_meta_tags_present():
    """OG + Twitter card + canonical are required for link previews to unfurl."""
    parsed = _parse_homepage()

    by_name = {m.get("name", ""): m.get("content", "") for m in parsed.meta}
    by_prop = {m.get("property", ""): m.get("content", "") for m in parsed.meta}

    assert by_name["description"], "page-level meta description must be non-empty"
    assert by_prop["og:title"], "og:title must be present"
    assert by_prop["og:description"], "og:description must be present"
    assert by_prop["og:type"] == "website"
    assert by_prop["og:url"].startswith("https://"), "og:url must be an https URL"
    assert by_prop["og:image"].startswith("https://"), "og:image must be an absolute https URL"
    assert by_name["twitter:card"] == "summary_large_image"
    assert by_name["twitter:title"], "twitter:title must be present"
    assert by_name["twitter:image"].startswith("https://")

    canonicals = [r for r in parsed.link_rels if r.get("rel") == "canonical"]
    assert canonicals, "a <link rel='canonical'> must be present"
    assert canonicals[0]["href"].startswith("https://")


def test_roadmap_section_html_and_data_hooks():
    """Roadmap section exists in HTML and leaderboard.json carries all required keys."""
    parsed = _parse_homepage()

    # HTML anchors for every data-driven slot
    assert "roadmap" in parsed.ids
    assert {"status-strip", "pack-grid", "phase-rail", "horizon-list"}.issubset(parsed.ids)
    assert "Roadmap" in " ".join(parsed.headings)

    # leaderboard.json must carry a populated roadmap block
    lb = json.loads((ROOT / "site" / "data" / "leaderboard.json").read_text())
    rm = lb.get("roadmap", {})
    assert rm.get("status"), "roadmap.status must be non-empty"
    assert rm["status"].get("benchmark_version"), "benchmark_version must be present"
    assert rm.get("packs"), "roadmap.packs must be non-empty"
    assert rm.get("phases"), "roadmap.phases must be non-empty"
    assert rm.get("design_doc"), "design_doc link must be present"
    assert rm.get("roadmap_doc"), "roadmap_doc link must be present"

    # app.js must have the rendering hooks
    app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "var roadmap = DATA.roadmap;" in app_js
    assert 'document.getElementById("pack-grid")' in app_js
    assert 'document.getElementById("phase-rail")' in app_js


def test_about_cite_section_html_and_data_hooks():
    """About / cite section exists in HTML with canary and CITATION.cff data."""
    parsed = _parse_homepage()

    assert "about" in parsed.ids
    assert {"cite-bibtex", "cite-apa", "cite-summary"}.issubset(parsed.ids)
    assert "canary-line" in parsed.classes or "canary" in parsed.classes

    # leaderboard.json must have a populated citation block
    lb = json.loads((ROOT / "site" / "data" / "leaderboard.json").read_text())
    cite = lb.get("citation", {})
    assert cite.get("bibtex", "").startswith("@"), "bibtex must start with @"
    assert cite.get("apa"), "APA citation must be non-empty"
    assert cite.get("title"), "citation title must be present"
    assert cite.get("version"), "citation version must be present"

    # app.js must render the citation block
    app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "var cite = DATA.citation;" in app_js
    assert 'document.getElementById("cite-bibtex")' in app_js


def test_homepage_freshness_signals_show_updated_date_and_version():
    """mb#671: header + footer carry a human-readable 'updated YYYY-MM-DD'
    plus the benchmark version, prerendered from leaderboard.json's
    machine-readable ``data_updated`` stamp."""
    import re

    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    payload = json.loads(
        (ROOT / "site" / "data" / "leaderboard.json").read_text(encoding="utf-8")
    )
    stamp = payload.get("data_updated")
    assert stamp, "leaderboard.json must carry a machine-readable data_updated"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
    date = stamp[:10]
    # Hero (header area) + footer both carry the prerendered freshness line.
    assert html.count(f"updated {date}") >= 2
    version = payload.get("benchmark_version")
    assert version and f"benchmark v{version}" in html


def test_blog_index_lists_a_dated_post():
    """mb#671 acceptance: the blog index lists at least one dated post."""
    import re

    html = (ROOT / "site" / "blog" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<time datetime="\d{4}-\d{2}-\d{2}">\d{4}-\d{2}-\d{2}</time>', html)
    assert 'href="arena-elo-decorrelation.html"' in html
    assert (ROOT / "site" / "blog" / "arena-elo-decorrelation.html").exists()
