from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROCESS_DATA = ROOT / "docs" / "rfc" / "data" / "capability_index_processes.yaml"
ELEMENT_DATA = ROOT / "docs" / "rfc" / "data" / "element_inventory.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _by_key(rows: list[dict], key: str) -> dict[str, dict]:
    indexed = {row[key]: row for row in rows}
    assert len(indexed) == len(rows)
    return indexed


def test_capability_index_process_yaml_has_required_schema_fields() -> None:
    data = _load_yaml(PROCESS_DATA)

    assert data["schema_version"] == "0.1.0"
    assert str(data["as_of_date"]) == "2026-06-15"
    assert data["source_docs"] == [
        "docs/DFM_RULES.md",
        "docs/DOMAIN_MATRIX.md",
        "tasks/registry.json",
    ]

    required_fields = {
        "process_id",
        "tier",
        "ci_level",
        "min_feature_mm",
        "max_envelope_mm",
        "kinematic_cadence",
        "energetic_threshold_J",
        "tolerance_floor_sigma",
        "source",
        "notes",
    }
    processes = data["processes"]
    assert len(processes) >= 10

    for row in processes:
        assert required_fields <= row.keys()
        assert row["tier"] in {"alpha", "beta", "v1"}
        assert row["ci_level"] in {"L0", "L1", "L2", "L3"}
        assert row["source"]
        assert row["notes"]


def test_capability_index_keeps_unmeasured_time_and_energy_axes_deferred() -> None:
    data = _load_yaml(PROCESS_DATA)

    for row in data["processes"]:
        assert row["kinematic_cadence"] is None
        assert row["energetic_threshold_J"] is None


def test_capability_index_gatable_process_boundaries_are_machine_readable() -> None:
    rows = _by_key(_load_yaml(PROCESS_DATA)["processes"], "process_id")

    fdm = rows["fdm_3d_print"]
    assert fdm["min_feature_mm"] == 1.0
    assert fdm["max_envelope_mm"] == [220, 220, 250]
    assert fdm["tolerance_floor_sigma"]["dimension_mm"] == 0.8
    assert fdm["tolerance_floor_sigma"]["mass_fraction_max"] == 0.5

    laser = rows["laser_2d"]
    assert laser["min_feature_mm"] == 2.5
    assert laser["min_web_mm"] == 8.0
    assert laser["max_envelope_mm"] == [300, 200]
    assert laser["tolerance_floor_sigma"]["kerf_clearance_target_mm"] == 0.1
    assert laser["tolerance_floor_sigma"]["kerf_clearance_tol_mm"] == 0.05

    sheet = rows["sheet_metal"]
    assert sheet["min_feature_mm"] is None
    assert sheet["min_feature_rules"]["min_inside_bend_radius"] == "r >= t"
    assert sheet["min_feature_rules"]["min_usable_flange_mm"] == "max(5.0, 3*t)"
    assert sheet["tolerance_floor_sigma"]["flat_length_mm"] == 0.5
    assert sheet["tolerance_floor_sigma"]["gauge_mm"] == 0.4

    injection = rows["injection_molding"]
    assert injection["min_feature_rules"]["min_draft_angle_deg"] == 1.0
    assert injection["min_feature_rules"]["rib_to_wall_ratio_max"] == 0.6
    assert injection["tolerance_floor_sigma"]["draft_deg"] == 0.2


def test_element_inventory_yaml_has_required_schema_fields() -> None:
    data = _load_yaml(ELEMENT_DATA)

    assert data["schema_version"] == "0.1.0"
    assert str(data["as_of_date"]) == "2026-06-15"
    assert "ESTIMATED" in data["source_note"]

    required_fields = {
        "symbol",
        "name",
        "crustal_abundance_ppm",
        "global_annual_run_rate_t",
        "scarcity_penalty",
        "abundant_substitute",
        "source",
        "estimated",
    }
    elements = data["elements"]
    assert len(elements) >= 15

    for row in elements:
        assert required_fields == row.keys()
        assert row["symbol"][0].isupper()
        assert row["name"]
        assert row["crustal_abundance_ppm"] > 0
        assert float(row["global_annual_run_rate_t"]) > 0
        assert 0.0 <= row["scarcity_penalty"] <= 1.0
        assert row["source"]
        assert row["estimated"] is True


def test_element_inventory_resource_triage_case_for_gold_emi() -> None:
    rows = _by_key(_load_yaml(ELEMENT_DATA)["elements"], "symbol")

    gold = rows["Au"]
    aluminium = rows["Al"]
    copper = rows["Cu"]

    assert gold["scarcity_penalty"] >= 0.95
    assert gold["abundant_substitute"] == "Al"
    assert "EMI" in gold["source"]

    assert aluminium["scarcity_penalty"] <= 0.05
    assert aluminium["abundant_substitute"] is None
    assert copper["scarcity_penalty"] < gold["scarcity_penalty"]
    assert copper["abundant_substitute"] == "Al"
