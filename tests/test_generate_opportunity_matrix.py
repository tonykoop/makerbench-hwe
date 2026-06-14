"""Opportunity Matrix generator (#120): JSON + heatmap HTML + Top Vacancies report.

Loads the script via importlib (repo convention, see test_run_navigation.py) and
exercises the three emitted surfaces against the live results/ tree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gen = _load("generate_opportunity_matrix")


class _WellFormed(HTMLParser):
    """Tiny structural check: tags balance (ignoring void elements)."""

    VOID = {"meta", "br", "hr", "img", "input", "link", "span"}  # span treated loose

    def __init__(self):
        super().__init__()
        self.stack = []
        self.ok = True

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()


def test_cli_writes_three_surfaces(tmp_path):
    out_json = tmp_path / "om.json"
    out_html = tmp_path / "om.html"
    out_report = tmp_path / "om.md"
    rc = gen.main([
        "--results-dir", str(ROOT / "results"),
        "--runs-root", str(ROOT / "results"),
        "--output-json", str(out_json),
        "--output-html", str(out_html),
        "--output-report", str(out_report),
    ])
    assert rc == 0
    assert out_json.exists() and out_html.exists() and out_report.exists()

    cube = json.loads(out_json.read_text(encoding="utf-8"))
    assert cube["schema"] == "makerbench-opportunity-matrix-v1"
    assert cube["counts"]["coordinates"] > 0
    assert "<table class='heat'>" in out_html.read_text(encoding="utf-8")
    assert "Top plugin vacancies" in out_report.read_text(encoding="utf-8")


def test_html_is_structurally_balanced(tmp_path):
    out_html = tmp_path / "om.html"
    gen.main([
        "--results-dir", str(ROOT / "results"),
        "--output-json", str(tmp_path / "om.json"),
        "--output-html", str(out_html),
        "--output-report", str(tmp_path / "om.md"),
    ])
    parser = _WellFormed()
    parser.feed(out_html.read_text(encoding="utf-8"))
    assert parser.stack == [], f"unclosed tags: {parser.stack}"


def test_heat_color_ramps_and_vacancy_alpha():
    low = gen._heat_color(0.0, vacancy=False)
    high = gen._heat_color(1.0, vacancy=False)
    assert low != high
    assert gen._heat_color(None, vacancy=False) == "#efe9dc"
    # vacancy cells are rendered semi-transparent
    assert "0.55" in gen._heat_color(0.8, vacancy=True)


def test_with_domain_flag_expands_cube(tmp_path):
    gen.main([
        "--results-dir", str(ROOT / "results"),
        "--with-domain",
        "--output-json", str(tmp_path / "om.json"),
        "--output-html", str(tmp_path / "om.html"),
        "--output-report", str(tmp_path / "om.md"),
    ])
    cube = json.loads((tmp_path / "om.json").read_text(encoding="utf-8"))
    assert cube["with_domain"] is True
    assert cube["axes"]["domain"]


def test_report_renders_proven_table_when_evidence_present(tmp_path):
    # fabricate a manifest so the proven branch of the report is exercised
    results = tmp_path / "results" / "claude-code-opus-4.8"
    results.mkdir(parents=True)
    (results / "blind.json").write_text(json.dumps({
        "model_identifier": "claude-code-opus-4.8",
        "results": [{"task_id": "t", "seed": 1, "track": "blind", "grade": {"score": 4}}],
    }), encoding="utf-8")
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "workflow_manifest.json").write_text(json.dumps({
        "stack": {"orchestrator": "claude-code-opus-4.8", "host_application": "blender",
                  "execution_bridge": "blender-mcp"},
        "dossier": {"verification": {"score": 4}},
        "hii": {"autonomy_ratio": 0.9},
    }), encoding="utf-8")
    gen.main([
        "--results-dir", str(tmp_path / "results"),
        "--runs-root", str(tmp_path / "runs"),
        "--output-json", str(tmp_path / "om.json"),
        "--output-html", str(tmp_path / "om.html"),
        "--output-report", str(tmp_path / "om.md"),
    ])
    report = (tmp_path / "om.md").read_text(encoding="utf-8")
    assert "Proven combinations" in report
    assert "Blender MCP" in report
