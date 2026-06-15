"""On-disk catalog-entry contract tests for the Unified Component Model.

These exercise the three-file (symbol.json + footprint.kicad_mod + model.step)
layer that lets MakerBench consume the shared ``offtheshelf`` catalog: file
presence, pin<->pad agreement, and a non-degenerate STEP bounding box.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from makerbench.unified_component import (
    CatalogEntryManifest,
    count_footprint_pads,
    count_symbol_pins,
    parse_step_bbox,
    validate_catalog_entry,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT / "examples" / "component_catalog"


def test_worked_example_0603_validates():
    report = validate_catalog_entry(CATALOG / "GENERIC-RES-0603")
    assert report.ok, report.errors
    assert report.category == "electronic"
    assert report.symbol_pin_count == 2
    assert report.footprint_pad_count == 2
    assert report.step_bbox_mm is not None
    assert min(report.step_bbox_mm) > 0.0


def test_worked_example_lqfp64_validates():
    report = validate_catalog_entry(CATALOG / "GENERIC-LQFP-64")
    assert report.ok, report.errors
    assert report.symbol_pin_count == 64
    assert report.footprint_pad_count == 64
    # 10x10 body, 12x12 over leads, 1.4 mm tall -> non-degenerate Z.
    assert report.step_bbox_mm[2] > 1.0


def test_manifest_ignores_offtheshelf_extra_keys():
    manifest = CatalogEntryManifest.model_validate(
        {
            "mpn": "X",
            "category": "electronic",
            "files": {"symbol": "symbol.json"},
            "physics": {"max_junction_temp_c": 150},
            "vendors": [{"distributor": "Digi-Key"}],
            "provenance": {"license": "CC-BY-4.0"},
            "tags": ["seed"],
        }
    )
    assert manifest.mpn == "X"
    assert manifest.files.footprint is None


def test_parse_step_bbox_and_degenerate():
    box = (
        "DATA;\n"
        "#1=CARTESIAN_POINT('',(0.,0.,0.));\n"
        "#2=CARTESIAN_POINT('',(2.0,3.0,4.0));\n"
        "ENDSEC;\n"
    )
    assert parse_step_bbox(box) == (2.0, 3.0, 4.0)
    flat = "#1=CARTESIAN_POINT('',(0.,0.,0.));\n#2=CARTESIAN_POINT('',(2.0,3.0,0.0));\n"
    assert parse_step_bbox(flat) == (2.0, 3.0, 0.0)
    assert parse_step_bbox("no points here") is None


def test_pin_and_pad_counters_exclude_mechanical():
    pins = count_symbol_pins({"pins": [{"number": "1"}, {"number": "2", "name": "B"}]})
    assert pins == {"1", "2"}
    # ~ placeholder pin names are ignored; pad "" and np_thru_hole are mechanical.
    pads = count_footprint_pads(
        '(pad "1" smd rect (at 0 0))\n'
        '(pad "2" thru_hole circle (at 1 0))\n'
        '(pad "" np_thru_hole circle (at 2 0))\n'
        '(pad "MH1" np_thru_hole circle (at 3 0))\n'
    )
    assert pads == {"1", "2"}


def test_missing_file_is_reported(tmp_path):
    src = CATALOG / "GENERIC-RES-0603"
    dst = tmp_path / "entry"
    shutil.copytree(src, dst)
    (dst / "model.step").unlink()
    report = validate_catalog_entry(dst)
    assert not report.ok
    assert any("model_step file missing" in e for e in report.errors)


def test_pin_pad_mismatch_is_reported(tmp_path):
    src = CATALOG / "GENERIC-RES-0603"
    dst = tmp_path / "entry"
    shutil.copytree(src, dst)
    symbol = json.loads((dst / "symbol.json").read_text())
    symbol["pins"].append({"number": "3", "name": "EXTRA", "type": "passive"})
    (dst / "symbol.json").write_text(json.dumps(symbol), encoding="utf-8")
    report = validate_catalog_entry(dst)
    assert not report.ok
    assert any("pin/pad mismatch" in e for e in report.errors)


def test_degenerate_step_is_reported(tmp_path):
    src = CATALOG / "GENERIC-RES-0603"
    dst = tmp_path / "entry"
    shutil.copytree(src, dst)
    (dst / "model.step").write_text(
        "DATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\n"
        "#2=CARTESIAN_POINT('',(1.6,0.8,0.));\nENDSEC;\n",
        encoding="utf-8",
    )
    report = validate_catalog_entry(dst)
    assert not report.ok
    assert any("degenerate" in e for e in report.errors)


def test_missing_manifest_is_reported(tmp_path):
    report = validate_catalog_entry(tmp_path)
    assert not report.ok
    assert any("no catalog manifest" in e for e in report.errors)
