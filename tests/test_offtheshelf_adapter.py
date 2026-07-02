"""Back-compat adapter tests (offtheshelf issue #4).

These use *embedded* offtheshelf metadata fixtures (copied from the real
tonykoop/offtheshelf checkout) rather than a live sibling-repo path, since
makerbench-hwe's CI does not clone offtheshelf. That keeps this suite
hermetic while still proving field-for-field round-trip fidelity against the
exact values published in ``makerbench/catalog/fasteners.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from makerbench.catalog.offtheshelf_adapter import (
    TOLERANCES,
    load_offtheshelf_fasteners,
    metadata_to_fastener_record,
)
from makerbench.parts import PartsLibrary, load_catalog

CATALOG_DIR = Path(__file__).resolve().parents[1] / "makerbench" / "catalog"


# Real offtheshelf components/mechanical/MB-SHCS-M3-08/metadata.yaml content
# (generators/migrate_makerbench_fasteners.py output, verbatim).
MB_SHCS_M3_08_YAML = """
mpn: MB-SHCS-M3-08
manufacturer: (MakerBench synthetic)
description: Socket head cap screw, M3x0.5, 8 mm, ISO 4762 / DIN 912.
category: mechanical
package: ISO 4762 M3
files:
  symbol: null
  footprint: null
  model_step: null
physical:
  length_mm: 8
  width_mm: 5.5
  height_mm: 3.0
  thread: M3
  pitch_mm: 0.5
  head_dia_mm: 5.5
  head_height_mm: 3.0
  clearance_hole_close_mm: 3.2
  clearance_hole_normal_mm: 3.4
  clearance_hole_free_mm: 3.6
  tap_drill_mm: 2.5
  material: alloy_steel
  drive: hex_socket
electrical: {}
provenance:
  license: CC-BY-4.0
  redistributable: true
  source: first-party migration from makerbench-hwe catalog/fasteners.json catalog_version 0.1.0
  source_url: null
tags:
- fastener
- screw
- socket-head-cap-screw
- shcs
- iso-4762
- din-912
- m3
- thread-m3
"""

# Real offtheshelf components/mechanical/MB-HSI-M3/metadata.yaml content.
MB_HSI_M3_YAML = """
mpn: MB-HSI-M3
manufacturer: (MakerBench synthetic)
description: Heat-set insert, M3, brass, synthetic MakerBench dimensions.
category: mechanical
package: heat-set insert M3
files:
  symbol: null
  footprint: null
  model_step: null
physical:
  length_mm: 4.0
  width_mm: 4.6
  height_mm: 4.0
  thread: M3
  outer_dia_mm: 4.6
  boss_hole_dia_mm: 4.0
  min_boss_wall_mm: 1.5
  material: brass
  notes: Recommended boss hole 4.0 mm; boss outer wall >= 1.5 mm around insert.
electrical: {}
provenance:
  license: CC-BY-4.0
  redistributable: true
  source: first-party migration from makerbench-hwe catalog/fasteners.json catalog_version 0.1.0
  source_url: null
tags:
- fastener
- insert
- heat-set-insert
- threaded-insert
- m3
- thread-m3
"""


def _committed_fasteners() -> dict:
    return json.loads((CATALOG_DIR / "fasteners.json").read_text(encoding="utf-8"))


def _committed_record(part_number: str) -> dict:
    return next(
        p for p in _committed_fasteners()["parts"] if p["part_number"] == part_number
    )


def test_metadata_to_fastener_record_round_trips_screw_exactly():
    metadata = yaml.safe_load(MB_SHCS_M3_08_YAML)

    record = metadata_to_fastener_record(metadata)

    assert record == _committed_record("MB-SHCS-M3-08")


def test_metadata_to_fastener_record_round_trips_insert_exactly():
    metadata = yaml.safe_load(MB_HSI_M3_YAML)

    record = metadata_to_fastener_record(metadata)

    assert record == _committed_record("MB-HSI-M3")


def test_metadata_to_fastener_record_rejects_unrecognized_category():
    with pytest.raises(ValueError, match="unrecognized fastener category"):
        metadata_to_fastener_record({"mpn": "NOT-A-FASTENER", "tags": ["passive"]})


def _write_offtheshelf_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "offtheshelf"
    for part_id, yaml_text in (
        ("MB-SHCS-M3-08", MB_SHCS_M3_08_YAML),
        ("MB-HSI-M3", MB_HSI_M3_YAML),
    ):
        part_dir = root / "components" / "mechanical" / part_id
        part_dir.mkdir(parents=True)
        (part_dir / "metadata.yaml").write_text(yaml_text, encoding="utf-8")
    return root


def test_load_offtheshelf_fasteners_matches_committed_catalog_shape(tmp_path):
    root = _write_offtheshelf_checkout(tmp_path)

    catalog = load_offtheshelf_fasteners(root)

    committed = _committed_fasteners()
    assert catalog["catalog_version"] == committed["catalog_version"]
    assert catalog["units"] == committed["units"]
    assert catalog["tolerances"] == committed["tolerances"] == TOLERANCES
    by_part = {p["part_number"]: p for p in catalog["parts"]}
    assert by_part["MB-SHCS-M3-08"] == _committed_record("MB-SHCS-M3-08")
    assert by_part["MB-HSI-M3"] == _committed_record("MB-HSI-M3")


def test_load_offtheshelf_fasteners_requires_mechanical_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_offtheshelf_fasteners(tmp_path / "not-a-checkout")


def test_load_offtheshelf_fasteners_requires_at_least_one_part(tmp_path):
    root = tmp_path / "offtheshelf"
    (root / "components" / "mechanical").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        load_offtheshelf_fasteners(root)


def test_load_catalog_default_behavior_is_unchanged():
    """No offtheshelf_root, no env var: byte-identical to before this adapter existed."""
    assert os.environ.get("MAKERBENCH_OFFTHESHELF_ROOT") is None

    catalog = load_catalog()

    committed = _committed_fasteners()
    part_numbers = {p["part_number"] for p in catalog["parts"]}
    assert part_numbers >= {p["part_number"] for p in committed["parts"]}
    assert catalog["catalog_version"] == committed["catalog_version"]
    assert catalog["tolerances"] == committed["tolerances"]
    # bearings/tubing still merged in as before.
    cats = {p["category"] for p in catalog["parts"]}
    assert "radial_ball_bearing" in cats
    assert "aluminum_round_tube" in cats


def test_load_catalog_offtheshelf_root_param_sources_fasteners_from_checkout(tmp_path):
    root = _write_offtheshelf_checkout(tmp_path)

    catalog = load_catalog(offtheshelf_root=root)

    fastener_parts = {
        p["part_number"]: p
        for p in catalog["parts"]
        if p["category"] in ("socket_head_cap_screw", "heat_set_insert")
    }
    assert fastener_parts == {
        "MB-SHCS-M3-08": _committed_record("MB-SHCS-M3-08"),
        "MB-HSI-M3": _committed_record("MB-HSI-M3"),
    }
    # bearings/tubing are untouched — still sourced from the packaged files.
    cats = {p["category"] for p in catalog["parts"]}
    assert "radial_ball_bearing" in cats
    assert "aluminum_round_tube" in cats


def test_load_catalog_offtheshelf_root_env_var(monkeypatch, tmp_path):
    root = _write_offtheshelf_checkout(tmp_path)
    monkeypatch.setenv("MAKERBENCH_OFFTHESHELF_ROOT", str(root))

    catalog = load_catalog()

    part_numbers = {p["part_number"] for p in catalog["parts"]}
    assert part_numbers == {"MB-SHCS-M3-08", "MB-HSI-M3"} | {
        p["part_number"]
        for p in catalog["parts"]
        if p["category"] in ("radial_ball_bearing", "aluminum_round_tube")
    }


def test_parts_library_accepts_offtheshelf_root(tmp_path):
    root = _write_offtheshelf_checkout(tmp_path)

    lib = PartsLibrary(offtheshelf_root=root)

    assert lib.get("MB-SHCS-M3-08") == _committed_record("MB-SHCS-M3-08")
    assert lib.get("MB-HSI-M3") == _committed_record("MB-HSI-M3")


def test_enclosure_fastener_validation_pathway_agrees_with_default_catalog(tmp_path):
    """The enclosure_fastened grader's fastener checks read `screw`/`insert`
    dicts straight out of PartsLibrary.get(); an offtheshelf-sourced catalog
    must produce byte-identical records for the parts that oracle depends on,
    so grading behavior (pass/fail, quality metrics) cannot change.
    """
    root = _write_offtheshelf_checkout(tmp_path)
    default_lib = PartsLibrary()
    offtheshelf_lib = PartsLibrary(offtheshelf_root=root)

    for part_number in ("MB-SHCS-M3-08", "MB-HSI-M3"):
        assert offtheshelf_lib.get(part_number) == default_lib.get(part_number)

    assert offtheshelf_lib.catalog["tolerances"] == default_lib.catalog["tolerances"]
