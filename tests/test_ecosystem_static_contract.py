"""Static homepage contract tests for the MakerBench ecosystem section."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EcosystemParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.links: set[str] = set()
        self._capture_heading: str | None = None
        self._buf: list[str] = []
        self.headings: list[str] = []

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

    def handle_data(self, data: str) -> None:
        if self._capture_heading:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._capture_heading:
            text = " ".join("".join(self._buf).split())
            if text:
                self.headings.append(text)
            self._capture_heading = None


def _parse_homepage() -> EcosystemParser:
    parser = EcosystemParser()
    parser.feed((ROOT / "site" / "index.html").read_text(encoding="utf-8"))
    return parser


def test_ecosystem_section_keeps_public_homepage_mount_points():
    parsed = _parse_homepage()

    assert "The MakerBench ecosystem" in parsed.headings
    assert {"ecosystem", "ecosystem-intro", "eco-map", "eco-legend", "ecosystem-grid"}.issubset(
        parsed.ids
    )
    assert {"eco-map", "eco-legend", "eco-grid"}.issubset(parsed.classes)
    assert (
        "https://github.com/tonykoop/makerbench-hwe/blob/main/docs/LANDSCAPE.md"
        in parsed.links
    )


def test_ecosystem_renderer_uses_data_payload_and_themeable_svg_classes():
    app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")

    assert "function renderEcosystem()" in app_js
    assert "var data = DATA.ecosystem;" in app_js
    assert "function buildEcosystemMap(nodes)" in app_js
    assert 'role="img"' in app_js
    assert "eco-edge" in app_js
    assert "eco-box eco-box-" in app_js
    assert "eco-sw-" in app_js
    assert "eco-tag private" in app_js


def test_ecosystem_kind_labels_cover_all_generated_node_kinds():
    app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")

    for kind, label in {
        "harness": "Harness",
        "integrity": "Private integrity",
        "satellite": "Capability repo",
        "surface": "Interactive surface",
    }.items():
        assert f"{kind}: \"{label}\"" in app_js
