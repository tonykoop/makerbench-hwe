"""Acceptance locks for the instrument workflow corpus story (#183)."""

from __future__ import annotations

import json
from pathlib import Path

from makerbench import best_combo_per_craft as bc
from makerbench.schema import WorkflowManifest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "INSTRUMENT_WORKFLOW_CORPUS.md"
MATRIX_DOC = ROOT / "docs" / "OPPORTUNITY_MATRIX.md"
DEMO_ROOT = ROOT / "examples" / "instrument_workflow_corpus"
REPORT_JSON = ROOT / "site" / "data" / "best-combo-per-craft.json"
REPORT_MD = ROOT / "docs" / "BEST_COMBO_PER_CRAFT.md"

EXPECTED_PROCESSES = {
    "wood_turning",
    "stave_joinery",
    "cnc_router",
    "laser_cut",
    "cnc_plasma",
    "sheet_metal_brake",
    "hand_power_tools",
}


def test_issue_183_docs_extend_matrix_with_process_axis_and_corpus_contract():
    corpus_doc = DOC.read_text(encoding="utf-8")
    matrix_doc = MATRIX_DOC.read_text(encoding="utf-8")

    for process in EXPECTED_PROCESSES:
        assert process in corpus_doc
    for instrument in ("flutes", "fujara", "tongue-drum", "trumpet-sheetmetal"):
        assert instrument in corpus_doc
    assert "examples/instrument_workflow_corpus/" in corpus_doc
    assert "private instrument CAD" in corpus_doc

    assert "D' — fabrication process / craft" in matrix_doc
    assert "--with-process" in matrix_doc
    assert "generate_best_combo_per_craft.py" in matrix_doc


def test_issue_183_generated_report_surfaces_process_axis_and_corpus():
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    report = bc.build_craft_report(top_n=10)

    assert data["schema"] == bc.SCHEMA
    assert {p["id"] for p in data["axes"]["process"]} == EXPECTED_PROCESSES
    assert data["counts"]["processes"] == len(EXPECTED_PROCESSES)
    assert data["counts"]["corpus_instruments"] >= 15
    assert data["corpus"] == report["corpus"]
    assert "Winning combo per craft" in REPORT_MD.read_text(encoding="utf-8")


def test_issue_183_public_demo_manifests_validate_and_cover_live_domains():
    manifests = sorted(DEMO_ROOT.glob("*.workflow_manifest.json"))
    assert {path.name for path in manifests} == {
        "laser_tongue_drum.workflow_manifest.json",
        "sheet_metal_horn.workflow_manifest.json",
        "wood_flute.workflow_manifest.json",
    }

    processes: set[str] = set()
    instruments: set[str] = set()
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        WorkflowManifest.model_validate(payload)
        processes |= bc.manifest_processes(payload)
        instruments.add(payload["dossier"]["instrument"])
        text = path.read_text(encoding="utf-8").lower()
        assert "private/oracles" not in text
        assert "oracle_threshold" not in text

    assert {"wood_turning", "laser_cut", "sheet_metal_brake"} <= processes
    assert {"flutes", "tongue-drum", "trumpet-sheetmetal"} == instruments


def test_issue_183_demo_manifests_promote_expected_craft_winners():
    report = bc.build_craft_report(runs_root=DEMO_ROOT, top_n=5)
    by_process = {craft["process"]: craft for craft in report["crafts"]}

    assert report["counts"]["crafts_with_evidence"] >= 3
    for process in ("wood_turning", "laser_cut", "sheet_metal_brake"):
        craft = by_process[process]
        assert craft["winner"] is not None
        assert craft["winner"]["has_evidence"] is True
        assert craft["winner"]["sources"][0].endswith(".workflow_manifest.json")
