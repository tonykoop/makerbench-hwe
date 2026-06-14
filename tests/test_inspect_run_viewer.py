"""Inspect-a-Run 3D viewer (mb#107): verdict derivation + three-panel layout.

Covers the additions generate_run_explorer.py grew for the deep-dive viewer —
the canvas/trace/verdict hero, the four named grader checks, the inlined viewer
module, and graceful degradation — plus a syntax gate on the JS module itself.
The pre-existing run-nav contract lives in test_run_navigation.py; this file only
exercises the #107 surface.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALPHA = ROOT / "tests" / "fixtures" / "run_nav" / "run_alpha_vented_plate"
INSPECT_JS = ROOT / "site" / "assets" / "inspect-run.js"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


explorer = _load("generate_run_explorer")


def _rv(levels=None, quality=None, **kw):
    """A minimal RunView carrying one result row, for verdict/layout unit tests."""
    grade = {"levels": levels or [], "quality": quality or {}}
    return explorer.RunView(
        run_id="t", run_dir=ROOT, result_rows=[{"task_id": "t", "grade": grade}], **kw
    )


# --------------------------------------------------------------------------
# verdict_checks — the four named grader checks
# --------------------------------------------------------------------------

def test_verdict_checks_maps_all_four_keys():
    rv = _rv(levels=[
        {"level": 2, "passed": True, "detail": "disjoint", "checks": {"no_interference": True}},
        {"level": 4, "passed": False, "detail": "screw bottoms out", "checks": {"valid_fastener_selection": False}},
        {"level": 3, "passed": False, "detail": "210 g", "checks": {"mass_under_target": False}},
        {"level": 4, "passed": True, "detail": "2.1mm", "checks": {"printable_min_wall": True}},
    ])
    by_key = {c["key"]: c for c in explorer.verdict_checks(rv)}
    assert [c["key"] for c in explorer.verdict_checks(rv)] == [
        "no-interference", "thread-pitch", "mass-constraint", "wall-thickness"]
    assert by_key["no-interference"]["status"] == "pass"
    assert by_key["thread-pitch"]["status"] == "fail"
    assert by_key["mass-constraint"]["status"] == "fail"
    assert by_key["wall-thickness"]["status"] == "pass"
    assert by_key["no-interference"]["detail"] == "disjoint"


def test_verdict_check_absent_is_na_not_fabricated():
    rv = _rv(levels=[{"level": 1, "passed": True, "detail": "compiled", "checks": {"compiles": True}}])
    by_key = {c["key"]: c for c in explorer.verdict_checks(rv)}
    assert all(by_key[k]["status"] == "n/a"
               for k in ("no-interference", "thread-pitch", "mass-constraint", "wall-thickness"))


def test_verdict_detail_falls_back_to_quality_readout():
    rv = _rv(
        levels=[{"level": 3, "passed": False, "detail": "", "checks": {"mass_under_target": False}}],
        quality={"mass_g": 210.4, "min_wall_mm": 1.4},
    )
    by_key = {c["key"]: c for c in explorer.verdict_checks(rv)}
    assert by_key["mass-constraint"]["detail"] == "mass 210.4 g"
    # wall-thickness check absent from levels -> n/a, but detail still anchors on quality
    assert by_key["wall-thickness"]["status"] == "n/a"
    assert by_key["wall-thickness"]["detail"] == "min wall 1.40 mm"


# --------------------------------------------------------------------------
# _inspect_layout — the three-panel hero
# --------------------------------------------------------------------------

def test_layout_emits_canvas_modes_and_check_cards():
    rv = _rv(
        levels=[{"level": 4, "passed": False, "detail": "thin", "checks": {"printable_min_wall": False}}],
        quality={"min_wall_mm": 1.4},
    )
    rv.artifact_3d_file = "model.stl"
    layout = explorer._inspect_layout(rv)
    assert 'data-artifact="model.stl"' in layout
    assert 'data-min-wall="1.4000"' in layout
    for mode in ("solid", "heatmap", "interference", "wireframe"):
        assert f'data-mode="{mode}"' in layout
    for key in ("no-interference", "thread-pitch", "mass-constraint", "wall-thickness"):
        assert f'data-check="{key}"' in layout
    # solid is the default pressed mode
    assert 'data-mode="solid" aria-pressed="true"' in layout


def test_layout_no_artifact_degrades_to_pending():
    layout = explorer._inspect_layout(_rv())
    assert "data-artifact" not in layout
    assert "canvas pending" in layout


def test_layout_step_artifact_is_not_renderable_inline():
    rv = _rv()
    rv.artifact_3d_file = "model.step"
    layout = explorer._inspect_layout(rv)
    # the canvas explains STEP can't be tessellated; no min-wall heat-map promised
    assert "can't be tessellated" in layout
    # head injects nothing for STEP
    assert explorer._inspect_head(rv) == ""


def test_interference_zones_pass_through_to_data_attr():
    rv = _rv(quality={"interference_zones": [{"center": [1, 2, 3], "radius": 0.5}]})
    rv.artifact_3d_file = "model.stl"
    layout = explorer._inspect_layout(rv)
    assert "data-interference=" in layout
    # round-trips: the JSON in the attribute parses back to the zone
    start = layout.index("data-interference='") + len("data-interference='")
    end = layout.index("'", start)
    zones = json.loads(layout[start:end])
    assert zones == [{"center": [1, 2, 3], "radius": 0.5}]


def test_layout_escapes_untrusted_artifact_name():
    rv = _rv()
    rv.artifact_3d_file = 'a"><script>x.stl'
    layout = explorer._inspect_layout(rv)
    assert "<script>x" not in layout
    assert "&lt;script&gt;" in layout or "&quot;" in layout


# --------------------------------------------------------------------------
# _inspect_head — inlined viewer module
# --------------------------------------------------------------------------

def test_head_inlines_module_and_importmap_for_renderable():
    rv = _rv()
    rv.artifact_3d_file = "model.glb"
    head = explorer._inspect_head(rv)
    assert "importmap" in head
    assert explorer.THREE_CDN in head
    assert "computeThickness" in head  # the module body is inlined
    assert '<script type="module">' in head


def test_head_empty_without_renderable_artifact():
    assert explorer._inspect_head(_rv()) == ""  # no artifact


# --------------------------------------------------------------------------
# full render against the alpha fixture
# --------------------------------------------------------------------------

def test_render_alpha_includes_inspect_section_and_trace():
    rv = explorer.load_run(ALPHA)
    html_text = explorer.render(rv)
    assert 'id="inspect"' in html_text
    assert "Workflow trace" in html_text
    assert "HII" in html_text
    # alpha's level-4 printable_min_wall failed -> wall-thickness card is a fail
    assert 'data-check="wall-thickness"' in html_text
    # renderable .glb -> module + importmap injected once
    assert html_text.count("importmap") >= 1
    assert "computeThickness" in html_text


def test_render_is_well_formed_html():
    rv = explorer.load_run(ALPHA)

    class Bal(HTMLParser):
        VOID = {"meta", "br", "hr", "img", "input", "source", "link", "area", "col"}

        def __init__(self):
            super().__init__()
            self.stack: list[str] = []
            self.ok = True

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.VOID:
                return
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            elif tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            else:
                self.ok = False

    p = Bal()
    # feed only up to the inlined module; the JS body legitimately contains
    # angle brackets in string literals that an HTML balance check can't parse.
    text = explorer.render(rv)
    head_split = text.index('<script type="module">')
    p.feed(text[:head_split])
    assert p.ok, "unbalanced tags before the inlined module"
    assert text.lstrip().lower().startswith("<!doctype html>")
    assert text.rstrip().endswith("</html>")


# --------------------------------------------------------------------------
# JS module syntax gate
# --------------------------------------------------------------------------

def test_inspect_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    r = subprocess.run([node, "--check", str(INSPECT_JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
