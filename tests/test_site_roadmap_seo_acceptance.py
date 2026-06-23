"""Acceptance locks for roadmap/status, SEO, and citation story (#175)."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class _HeadAndAnchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[dict[str, str]] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if "id" in attr:
            self.ids.add(attr["id"])
        if tag == "meta":
            key = attr.get("name") or attr.get("property")
            if key:
                self.meta[key] = attr.get("content", "")
        if tag == "link":
            self.links.append(attr)


def _parse() -> _HeadAndAnchors:
    parser = _HeadAndAnchors()
    parser.feed((SITE / "index.html").read_text(encoding="utf-8"))
    return parser


def test_issue_175_homepage_exposes_roadmap_about_and_preview_metadata():
    page = _parse()

    assert {"roadmap", "status-strip", "pack-grid", "phase-rail", "horizon-list"}.issubset(
        page.ids
    )
    assert {"about", "cite-bibtex", "cite-apa", "cite-summary"}.issubset(page.ids)

    assert page.meta["description"]
    assert page.meta["og:title"]
    assert page.meta["og:description"]
    assert page.meta["og:type"] == "website"
    assert page.meta["og:url"].startswith("https://")
    assert page.meta["og:image"].startswith("https://")
    assert page.meta["twitter:card"] == "summary_large_image"
    assert page.meta["twitter:image"].startswith("https://")
    assert any(link.get("rel") == "canonical" and link.get("href", "").startswith("https://")
               for link in page.links)


def test_issue_175_roadmap_payload_is_registry_derived_and_statusful():
    payload = json.loads((SITE / "data" / "leaderboard.json").read_text())
    roadmap = payload["roadmap"]
    registry = json.loads((ROOT / "tasks" / "registry.json").read_text())

    assert roadmap["status"]["benchmark_version"] == registry["benchmark_version"]
    assert roadmap["status"]["benchmark_profile"] == registry["benchmark_profile"]
    assert roadmap["status"]["n_task_families"] == len(registry["task_families"])
    assert roadmap["status"]["n_packs"] == len(registry["task_packs"])
    assert roadmap["packs"]
    assert any(pack["live"] for pack in roadmap["packs"])
    assert any(not pack["live"] for pack in roadmap["packs"])
    assert roadmap["phases"]
    assert roadmap["horizon"]
    assert roadmap["design_doc"] == "docs/DESIGN.md"
    assert roadmap["roadmap_doc"] == "docs/ROADMAP.md"


def test_issue_175_citation_payload_matches_citation_cff():
    payload = json.loads((SITE / "data" / "leaderboard.json").read_text())
    citation = payload["citation"]
    cff = yaml.safe_load((ROOT / "CITATION.cff").read_text())

    assert citation["title"] == cff["title"]
    assert citation["version"] == cff["version"]
    assert citation["license"] == cff["license"]
    assert citation["url"] == cff["url"]
    assert citation["year"] == cff["date-released"].split("-", 1)[0]
    assert "Koop, Tony" in citation["apa"]
    assert citation["bibtex"].startswith("@software{makerbench_hwe")
