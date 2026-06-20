"""Static contract tests for explorer.html v2 (mb#165)."""

from __future__ import annotations

import json
import importlib.util
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "makerbench_site_build_data_explorer_contract",
    ROOT / "site" / "build_data.py",
)
build_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_data)


class ExplorerHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.scripts: list[dict[str, str]] = []
        self.importmaps: list[str] = []
        self._capture_importmap = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if "id" in attr:
            self.ids.add(attr["id"])
        for cls in attr.get("class", "").split():
            self.classes.add(cls)
        if tag == "script":
            self.scripts.append(attr)
            if attr.get("type") == "importmap":
                self._capture_importmap = True
                self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture_importmap:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture_importmap:
            self.importmaps.append("".join(self._buf).strip())
            self._capture_importmap = False


def _parse_explorer() -> ExplorerHTMLParser:
    parser = ExplorerHTMLParser()
    parser.feed((ROOT / "site" / "explorer.html").read_text(encoding="utf-8"))
    return parser


def test_explorer_html_keeps_three_pane_static_sandbox_contract():
    parsed = _parse_explorer()

    assert {"context-switch", "context-summary", "pane-matrix", "pane-viewport", "pane-engine"}.issubset(
        parsed.ids
    )
    assert {"sandbox", "pane", "left", "center", "right", "vp-canvas"}.issubset(parsed.classes)
    assert any(
        script.get("type") == "module"
        and script.get("src", "").startswith("assets/explorer.js")
        for script in parsed.scripts
    )


def test_explorer_importmap_pins_three_js_without_a_build_step():
    parsed = _parse_explorer()
    assert len(parsed.importmaps) == 1

    imports = json.loads(parsed.importmaps[0])["imports"]
    three = imports["three"]
    addons = imports["three/addons/"]

    assert three == "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"
    assert addons == "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"


def test_explorer_controller_matches_generated_context_layers():
    js = (ROOT / "site" / "assets" / "explorer.js").read_text(encoding="utf-8")

    assert 'const DATA_URL = "data/explorer.json";' in js
    layer_pairs = re.findall(
        r"^\s{2}([a-z0-9_]+): \{\n\s+id: \"([a-z0-9_]+)\"",
        js,
        re.MULTILINE,
    )
    layer_ids = {key for key, declared_id in layer_pairs if key == declared_id}
    expected_layers = {
        layer
        for ctx in [
            {"viewport": {"layers": ["mesh", "force", "fpv"]}},
            *build_data.EXPLORER_SCAFFOLD_CONTEXTS,
        ]
        for layer in ctx["viewport"]["layers"]
    }

    assert expected_layers == {"mesh", "force", "fpv"}
    assert expected_layers.issubset(layer_ids)
