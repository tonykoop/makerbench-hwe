"""Tests for the harder laser/vector frontier ladder (issue #118).

Covers the public oracle-free grader primitives (which compose makerbench.vector) and the
registry scaffold's leaderboard-separation guarantees for the second frontier ladder.
"""

from __future__ import annotations

from makerbench import laser_vector_ladder as lvl
from makerbench.task_packs import load_task_registry


# --- grader primitives -----------------------------------------------------

def _svg(body: str, w: int = 100, h: int = 60) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
        f'viewBox="0 0 {w} {h}">{body}</svg>'
    )


def test_kerf_fit_clearance_kerf_widens_slot_narrows_tab():
    out = lvl.kerf_fit_clearance(6.0, 5.7, 0.2)
    # effective slot 6.2, tab 5.5, clearance = 0.7
    assert out["effective_slot_mm"] == 6.2
    assert out["effective_tab_mm"] == 5.5
    assert out["clearance_mm"] == 0.7
    assert out["interference"] == 0.0


def test_kerf_fit_clearance_on_target_within_tolerance():
    out = lvl.kerf_fit_clearance(5.8, 5.9, 0.1, target_clearance_mm=0.1, tol_mm=0.05)
    assert out["clearance_mm"] == 0.1
    assert out["within_tolerance"] == 1.0


def test_kerf_fit_clearance_flags_interference():
    out = lvl.kerf_fit_clearance(5.0, 6.0, 0.1)
    assert out["clearance_mm"] < 0
    assert out["interference"] == 1.0
    assert out["within_tolerance"] == 0.0


def test_nesting_material_yield_legal_layout():
    nest = _svg('<rect x="5" y="5" width="40" height="50"/>'
                '<rect x="55" y="5" width="40" height="50"/>')
    out = lvl.nesting_material_yield(nest, 100, 60, min_gap_mm=5)
    assert out["part_count"] == 2.0
    assert out["used_area_mm2"] == 4000.0
    assert out["yield_fraction"] == round(4000 / 6000, 6)
    assert out["within_stock"] == 1.0
    assert out["non_overlapping"] == 1.0
    assert out["min_part_gap_mm"] == 10.0
    assert out["legal"] == 1.0


def test_nesting_material_yield_detects_overlap():
    ov = _svg('<rect x="5" y="5" width="40" height="50"/>'
              '<rect x="30" y="5" width="40" height="50"/>')
    out = lvl.nesting_material_yield(ov, 100, 60)
    assert out["non_overlapping"] == 0.0
    assert out["legal"] == 0.0


def test_nesting_material_yield_detects_out_of_stock():
    nest = _svg('<rect x="5" y="5" width="40" height="50"/>'
                '<rect x="55" y="5" width="40" height="50"/>')
    out = lvl.nesting_material_yield(nest, 80, 60)  # 95mm wide nest > 80mm stock
    assert out["within_stock"] == 0.0
    assert out["legal"] == 0.0


def test_nesting_material_yield_violates_min_gap():
    nest = _svg('<rect x="5" y="5" width="40" height="50"/>'
                '<rect x="55" y="5" width="40" height="50"/>')  # gap = 10mm
    out = lvl.nesting_material_yield(nest, 100, 60, min_gap_mm=15)
    assert out["min_part_gap_mm"] == 10.0
    assert out["legal"] == 0.0


def test_nesting_material_yield_rejects_unparsable():
    out = lvl.nesting_material_yield("not a vector", 100, 60)
    assert out["legal"] == 0.0
    assert out["part_count"] == 0.0


def test_path_rejection_flags_accepts_valid():
    ok = _svg('<rect x="1" y="1" width="8" height="8"/>', w=10, h=10)
    out = lvl.path_rejection_flags(ok)
    assert out["ok"] == 1.0
    assert out["n_rejections"] == 0.0
    assert all(out[code] == 0.0 for code in lvl.REJECTION_CODES)


def test_path_rejection_flags_flags_open_path():
    bad = _svg('<path d="M0 0 L10 0 L10 10"/>', w=10, h=10)
    out = lvl.path_rejection_flags(bad)
    assert out["ok"] == 0.0
    assert out["open_path"] == 1.0


def test_path_rejection_flags_flags_ambiguous_units():
    amb = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
           'viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8"/></svg>')
    out = lvl.path_rejection_flags(amb)
    assert out["ok"] == 0.0
    assert out["ambiguous_units"] == 1.0


def test_path_rejection_flags_has_complete_fixed_shape():
    out = lvl.path_rejection_flags(_svg('<rect x="1" y="1" width="8" height="8"/>', 10, 10))
    for code in lvl.REJECTION_CODES:
        assert code in out


# --- registry scaffold isolation -------------------------------------------

def test_builtin_registry_laser_vector_ladder_is_isolated():
    reg = load_task_registry("tasks/registry.json")
    assert reg.frontier_ladders is not None
    laser = [
        ladder for ladder in reg.frontier_ladders.ladders
        if ladder.doc == "docs/LASER_VECTOR_LADDER.md"
    ]
    assert len(laser) == 1
    rungs = laser[0].rungs
    rung_ids = {r.id for r in rungs}
    assert rung_ids == {
        "laser_vector_kerf_fit",
        "laser_vector_nesting_yield",
        "laser_vector_invalid_path",
    }
    family_ids = {f.id for f in reg.task_families}
    axis_family_ids = {fid for a in reg.capability_axes for fid in a.task_families}
    assert rung_ids.isdisjoint(family_ids)
    assert rung_ids.isdisjoint(axis_family_ids)
    # No collision with the existing native-vector / calibrator laser families.
    assert rung_ids.isdisjoint({
        "laser_tab_slot_panel", "laser_vector_tab_slot_panel", "laser_tab_slot_panel_tight",
    })
    # Documentary scaffold only: nothing is live (no private oracle yet).
    assert all(r.status != "live" for r in rungs)
    # Every named primitive resolves to a real public function.
    for rung in rungs:
        for name in rung.grader_primitives:
            assert callable(getattr(lvl, name))


def test_frontier_ladders_have_both_sheet_metal_and_laser_vector():
    reg = load_task_registry("tasks/registry.json")
    docs = {ladder.doc for ladder in reg.frontier_ladders.ladders}
    assert "docs/SHEET_METAL_LADDER.md" in docs
    assert "docs/LASER_VECTOR_LADDER.md" in docs
