"""Acceptance locks for the Unified Component Model story (#208)."""

from __future__ import annotations

from pathlib import Path

import yaml

from makerbench.unified_component import find_catalog_manifest, validate_catalog_entry

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT / "examples" / "component_catalog"
DOC = REPO_ROOT / "docs" / "UNIFIED_COMPONENT_MODEL.md"

EXPECTED_ENTRY_FILES = {
    "metadata.yaml",
    "symbol.json",
    "footprint.kicad_mod",
    "model.step",
}


def test_issue_208_docs_define_schema_examples_and_public_boundary():
    text = DOC.read_text(encoding="utf-8")

    assert "symbol.json" in text
    assert "footprint.kicad_mod" in text
    assert "model.step" in text
    assert "metadata.yaml" in text
    assert "GENERIC-RES-0603" in text
    assert "GENERIC-LQFP-64" in text
    assert "validate_catalog_entry(entry_dir)" in text
    assert "public fixtures" in text
    assert "no oracle thresholds" in text
    assert "private/oracles/" in text


def test_issue_208_worked_examples_are_exact_three_file_manifest_entries():
    entries = sorted(p for p in CATALOG.iterdir() if p.is_dir())
    assert {p.name for p in entries} == {"GENERIC-LQFP-64", "GENERIC-RES-0603"}

    for entry in entries:
        files = {p.name for p in entry.iterdir() if p.is_file()}
        assert files == EXPECTED_ENTRY_FILES

        manifest_path = find_catalog_manifest(entry)
        assert manifest_path is not None
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert manifest["category"] == "electronic"
        assert manifest["files"] == {
            "symbol": "symbol.json",
            "footprint": "footprint.kicad_mod",
            "model_step": "model.step",
        }
        assert manifest["provenance"]["redistributable"] is True
        assert manifest["provenance"]["license"]
        assert "oracle" not in manifest_path.read_text(encoding="utf-8").lower()


def test_issue_208_validator_accepts_examples_with_pin_pad_and_step_evidence():
    expected_counts = {"GENERIC-RES-0603": 2, "GENERIC-LQFP-64": 64}

    for entry_id, expected_count in expected_counts.items():
        entry = CATALOG / entry_id
        manifest = yaml.safe_load((entry / "metadata.yaml").read_text(encoding="utf-8"))
        report = validate_catalog_entry(entry)

        assert report.ok, report.errors
        assert report.symbol_pin_count == expected_count
        assert report.footprint_pad_count == expected_count
        assert report.symbol_pin_count == report.footprint_pad_count
        assert report.step_bbox_mm is not None
        assert min(report.step_bbox_mm) > 0.0
        assert (
            abs(report.step_bbox_mm[2] - manifest["physical"]["height_mm"])
            <= 0.5
        )
