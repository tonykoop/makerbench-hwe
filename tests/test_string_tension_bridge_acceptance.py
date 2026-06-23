"""Issue #131 acceptance lock for instrument string-tension structural gates.

The underlying bridge and soundboard tasks already have detailed geometry and
negative-control tests. This file keeps the story-level contract visible:
public params feed deterministic structural formulas, the checks expose the
named bridge/soundboard load outcomes, and quarterly challenge thresholds stay
private rather than leaking into public fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

from makerbench import instrument_acoustics_ladder as ial
from makerbench.runner import load_task

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "INSTRUMENT_ACOUSTICS_LADDER.md"
REGISTRY = ROOT / "tasks" / "registry.json"

ISSUE_INPUT_KEYS = {
    "material_process",
    "string_count",
    "tension_class",
    "break_angle_deg",
    "bridge_span_mm",
    "bridge_footprint_depth_mm",
    "section_thickness_mm",
    "load_path_declared",
}

ISSUE_CHECK_KEYS = {
    "min_wall_under_load_ok",
    "bridge_deflection_within_limit",
    "load_path_declared",
    "feasible",
}


def _instrument_ladder_rungs() -> dict[str, dict]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ladder = next(
        item for item in registry["frontier_ladders"]["ladders"]
        if item["doc"] == "docs/INSTRUMENT_ACOUSTICS_LADDER.md"
    )
    return {rung["id"]: rung for rung in ladder["rungs"]}


def test_bridge_primitive_exposes_issue_131_inputs_and_checks():
    params = {
        "material_process": "cnc_hardwood",
        "string_count": 6,
        "tension_class": "medium",
        "break_angle_deg": 12.0,
        "bridge_span_mm": 100.0,
        "bridge_footprint_depth_mm": 24.0,
        "section_thickness_mm": 18.0,
        "load_path_declared": True,
    }

    out = ial.string_tension_bridge_check(params)

    assert ISSUE_INPUT_KEYS.issubset(params)
    assert ISSUE_CHECK_KEYS.issubset(out)
    assert out["total_string_tension_n"] > 0.0
    assert out["downforce_n"] > 0.0
    assert out["required_section_thickness_mm"] > 0.0
    assert out["bridge_deflection_within_limit"] == 1.0
    assert out["min_wall_under_load_ok"] == 1.0
    assert out["load_path_declared"] == 1.0
    assert out["feasible"] == 1.0


def test_bridge_primitive_fails_structural_or_load_path_regressions():
    base = {
        "material_process": "fdm_pla",
        "string_count": 6,
        "tension_class": "medium",
        "break_angle_deg": 14.0,
        "bridge_span_mm": 120.0,
        "bridge_footprint_depth_mm": 16.0,
        "section_thickness_mm": 1.0,
        "load_path_declared": True,
    }
    thin = ial.string_tension_bridge_check(base)
    no_path = ial.string_tension_bridge_check({
        **base,
        "section_thickness_mm": 18.0,
        "load_path_declared": False,
    })

    assert thin["bridge_deflection_within_limit"] == 0.0
    assert thin["min_wall_under_load_ok"] == 0.0
    assert thin["feasible"] == 0.0
    assert no_path["load_path_declared"] == 0.0
    assert no_path["feasible"] == 0.0


def test_string_tension_tasks_are_public_param_derived_not_oracle_backed():
    for task_id in ("acoustics_string_tension_bridge", "acoustics_soundboard_panel"):
        task = load_task(task_id)
        spec = task.make_spec(0)
        source = task.module.realize_oracle_scad(spec)

        assert task.module.ORACLE_PATH is None
        assert spec.allowed_tools == []
        assert "load_path_declared" in spec.brief
        assert "MAKERBENCH-" in source
        assert "private" not in source.lower()


def test_registry_keeps_string_tension_live_but_off_leaderboard():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rungs = _instrument_ladder_rungs()
    family_ids = {family["id"] for family in registry["task_families"]}
    axis_ids = {
        family_id
        for axis in registry["capability_axes"]
        for family_id in axis["task_families"]
    }

    bridge = rungs["acoustics_string_tension_bridge"]
    soundboard = rungs["acoustics_soundboard_panel"]

    assert bridge["status"] == "live"
    assert bridge["grader_primitives"] == ["string_tension_bridge_check"]
    assert bridge["private_fixtures"] == []
    assert "Quarterly challenge load/threshold tuning still stays private" in (
        bridge["deferred_reason"]
    )
    assert soundboard["status"] == "live"
    assert soundboard["grader_primitives"] == ["soundboard_panel_deflection_check"]

    assert {"acoustics_string_tension_bridge", "acoustics_soundboard_panel"}.isdisjoint(
        family_ids
    )
    assert {"acoustics_string_tension_bridge", "acoustics_soundboard_panel"}.isdisjoint(
        axis_ids
    )


def test_public_docs_name_bridge_and_soundboard_formula_shapes():
    text = DOC.read_text(encoding="utf-8")

    for needle in (
        "makerbench-hwe#131",
        "string_tension_bridge_check",
        "soundboard_panel_deflection_check",
        "span / 400",
        "simply-supported-beam deflection",
        "uniform pressure",
        "public gold is param-derived",
        "ORACLE_PATH=None",
        "Promotion of a runnable rung to the **scored leaderboard** is a separate",
    ):
        assert needle in text
