"""Run-navigation generators (mb#104): per-run explorer + cross-run library.

Loads the scripts via importlib (repo convention, see test_channel_comparison.py)
and runs them against the two fixture run dirs:
  - run_alpha_vented_plate: full partner stubs (packet, WorkflowManifest/HII, cert, 3D, video)
  - run_beta_bracket: bare run.json only -> every partner slot must degrade to "pending"
"""

from __future__ import annotations

import importlib.util
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "run_nav"
ALPHA = FIXTURES / "run_alpha_vented_plate"
BETA = FIXTURES / "run_beta_bracket"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


explorer = _load("generate_run_explorer")
library = _load("generate_run_library")


class _WellFormed(HTMLParser):
    """Minimal balance check: every opened non-void tag is closed."""
    VOID = {"meta", "br", "hr", "img", "input", "source", "link", "area", "col"}

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.ok = True

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:  # tolerate optional-close (e.g. <li>, <p>)
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            self.ok = False


def _assert_html(text: str):
    assert text.lstrip().lower().startswith("<!doctype html>")
    assert "</html>" in text
    p = _WellFormed()
    p.feed(text)
    assert p.ok, "unbalanced tags"


# --------------------------------------------------------------------------
# explorer
# --------------------------------------------------------------------------

def test_explorer_alpha_renders_all_slots():
    rv = explorer.load_run(ALPHA)
    assert rv.harness_class == "agentic-cad"
    assert rv.domain == "sheet-metal"
    assert rv.has_packet and rv.has_manifest and rv.has_certificate
    assert rv.verification == "verified"
    assert rv.hii == "L1"
    assert rv.artifact_3d_file and rv.video_file
    html_text = explorer.render(rv)
    _assert_html(html_text)
    # real grader verdict surfaced (level 4 failed in the fixture)
    assert "vented_plate" in html_text
    assert "FAIL" in html_text and "PASS" in html_text
    # packet + trace + cert links present, no "pending" leaking into populated slots
    assert "packet/gdt.pdf" in html_text
    assert "HII L1" in html_text
    assert rv.certificate_file in html_text


def test_explorer_beta_degrades_to_pending():
    rv = explorer.load_run(BETA)
    assert rv.harness_class == "unclassified"
    assert not rv.has_packet and not rv.has_manifest and not rv.has_certificate
    assert rv.verification == "pending"
    html_text = explorer.render(rv)
    _assert_html(html_text)
    # every partner-owned slot degrades — viewer, packet, manifest, cert, video
    assert html_text.count("pending") >= 5
    # but the real grader verdict still renders
    assert "sheet_metal_bracket" in html_text


def test_explorer_score_is_mean_of_results():
    rv = explorer.load_run(ALPHA)
    assert abs(rv.score - 0.75) < 1e-9


# --------------------------------------------------------------------------
# library + manifest
# --------------------------------------------------------------------------

def test_library_manifest_has_two_entries(tmp_path):
    views = library.scan_runs(FIXTURES)
    assert len(views) == 2
    manifest = library.build_manifest(views, FIXTURES)
    assert manifest["schema"] == "makerbench-runs-manifest-v1"
    assert manifest["count"] == 2
    ids = {r["run_id"] for r in manifest["runs"]}
    assert ids == {"run_alpha_vented_plate", "run_beta_bracket"}
    # explorer paths are relative to the runs root
    for r in manifest["runs"]:
        assert r["explorer"].endswith("/explorer.html")
        assert r["explorer"].startswith(r["run_id"])
    # round-trips as JSON
    out = tmp_path / "runs-manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    assert json.loads(out.read_text())["count"] == 2


def test_library_html_valid_and_filterable():
    views = library.scan_runs(FIXTURES)
    manifest = library.build_manifest(views, FIXTURES)
    html_text = library.render_library_html(manifest)
    _assert_html(html_text)
    assert html_text.count('<article class="card"') == 2
    # all five required filter axes present
    for key in ("harness-class", "domain", "hii", "verification", "score-band"):
        assert f'data-filter-key="{key}"' in html_text
    # search box present
    assert 'id="search"' in html_text


def test_score_band_buckets():
    assert library.score_band(0.95) == "0.9-1.0"
    assert library.score_band(0.75) == "0.7-0.9"
    assert library.score_band(0.5) == "0.5-0.7"
    assert library.score_band(0.1) == "<0.5"
    assert library.score_band(None) == "ungraded"
