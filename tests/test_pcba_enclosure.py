"""Tests for public PCBA/enclosure interference primitives (#209)."""

from __future__ import annotations

import pytest

from makerbench.pcba_enclosure import (
    connector_cutout_alignment,
    grade_pcba_enclosure,
    make_pcba_enclosure_case,
    pcba_enclosure_interference_dual_gate,
)


def _enclosure(**overrides):
    params = {
        "internal_height_mm": 12.0,
        "board_thickness_mm": 1.6,
        "required_z_clearance_mm": 1.0,
        "keepout_clearance_mm": 0.0,
    }
    params.update(overrides)
    return params


def _component(**overrides):
    params = {
        "ref": "U1",
        "x_mm": 10.0,
        "y_mm": 10.0,
        "width_mm": 4.0,
        "depth_mm": 4.0,
        "height_mm": 5.0,
    }
    params.update(overrides)
    return params


def _keepout(**overrides):
    params = {
        "id": "boss",
        "x_mm": 24.0,
        "y_mm": 10.0,
        "width_mm": 4.0,
        "depth_mm": 4.0,
    }
    params.update(overrides)
    return params


def _check(components=None, keepouts=None, enclosure=None):
    return pcba_enclosure_interference_dual_gate(
        components or [_component()],
        keepouts or [_keepout()],
        enclosure or _enclosure(),
    )


def test_pcba_enclosure_dual_gate_passes_clean_layout():
    out = _check()

    assert out["z_height_clearance_pass"] == 1.0
    assert out["keepout_clearance_pass"] == 1.0
    assert out["dual_gate_pass"] == 1.0
    assert out["min_z_clearance_mm"] == pytest.approx(5.4)
    assert out["min_keepout_gap_mm"] == pytest.approx(10.0)


def test_pcba_enclosure_tall_component_fails_z_gate_only():
    out = _check(components=[_component(height_mm=10.0)])

    assert out["z_height_clearance_pass"] == 0.0
    assert out["keepout_clearance_pass"] == 1.0
    assert out["dual_gate_pass"] == 0.0
    assert out["min_z_clearance_mm"] == pytest.approx(0.4)


def test_pcba_enclosure_keepout_overlap_fails_keepout_gate_only():
    out = _check(keepouts=[_keepout(x_mm=11.0, y_mm=10.0)])

    assert out["z_height_clearance_pass"] == 1.0
    assert out["keepout_clearance_pass"] == 0.0
    assert out["dual_gate_pass"] == 0.0
    assert out["keepout_violation_count"] == 1.0
    assert out["max_keepout_overlap_area_mm2"] == pytest.approx(12.0)
    assert out["min_keepout_gap_mm"] == pytest.approx(-3.0)


def test_pcba_enclosure_keepout_clearance_margin_inflates_exclusion():
    # Physical footprint gap is 0.2 mm, but the enclosure keepout requires 0.5 mm.
    out = _check(
        keepouts=[_keepout(x_mm=14.2, y_mm=10.0)],
        enclosure=_enclosure(keepout_clearance_mm=0.5),
    )

    assert out["keepout_clearance_pass"] == 0.0
    assert out["max_keepout_overlap_area_mm2"] == pytest.approx(1.2)
    assert out["min_keepout_gap_mm"] == pytest.approx(-0.3)


def test_pcba_enclosure_z_bounded_keepout_ignores_non_overlapping_volume():
    out = _check(
        components=[_component(z_base_mm=6.0, height_mm=3.0)],
        keepouts=[_keepout(x_mm=10.0, y_mm=10.0, z_min_mm=0.0, z_max_mm=4.0)],
    )

    assert out["keepout_clearance_pass"] == 1.0
    assert out["dual_gate_pass"] == 1.0
    assert out["keepout_violation_count"] == 0.0
    assert out["min_keepout_gap_mm"] == pytest.approx(-4.0)


def test_pcba_enclosure_bottom_side_component_uses_board_underside_z():
    out = _check(
        components=[
            _component(ref="J1", side="bottom", z_base_mm=0.0, height_mm=2.0),
        ],
        keepouts=[_keepout(x_mm=10.0, y_mm=10.0, z_min_mm=-3.0, z_max_mm=-1.0)],
    )

    assert out["keepout_clearance_pass"] == 0.0
    assert out["dual_gate_pass"] == 0.0
    assert out["component_max_z_mm"] == 1.6
    assert out["min_z_clearance_mm"] == pytest.approx(10.4)


# --- Connector cutout alignment + full mechanical dual-gate grader (#209) ---


def test_connector_cutout_alignment_aligned_and_misaligned():
    connectors = [{"ref": "J1", "wall": "x_max", "pos_mm": 16.0, "width_mm": 9.0}]
    cutouts = [{"id": "usb_c", "wall": "x_max", "pos_mm": 16.0, "width_mm": 10.0}]
    [rec] = connector_cutout_alignment(connectors, cutouts)
    assert rec["aligned"] is True
    assert rec["matched_cutout"] == "usb_c"
    assert rec["center_delta_mm"] == 0.0

    [shifted] = connector_cutout_alignment(
        [{"ref": "J1", "wall": "x_max", "pos_mm": 19.0, "width_mm": 9.0}], cutouts
    )
    assert shifted["aligned"] is False
    assert shifted["center_delta_mm"] == pytest.approx(3.0)
    assert shifted["protrusion_mm"] == pytest.approx(2.5)


def test_connector_with_no_matching_wall_cutout_is_unaligned():
    [rec] = connector_cutout_alignment(
        [{"ref": "J1", "wall": "y_min", "pos_mm": 10.0, "width_mm": 5.0}],
        [{"id": "c", "wall": "x_max", "pos_mm": 10.0, "width_mm": 8.0}],
    )
    assert rec["aligned"] is False
    assert rec["matched_cutout"] is None


def test_parametric_case_clears_all_three_gates():
    case = make_pcba_enclosure_case()
    report = grade_pcba_enclosure(
        case["components"],
        case["keepouts"],
        case["enclosure"],
        connectors=case["connectors"],
        cutouts=case["cutouts"],
    )
    assert report.dual_gate_pass is True
    assert report.z_height_clearance_pass is True
    assert report.keepout_clearance_pass is True
    assert report.connector_cutout_pass is True
    assert report.collisions == []
    assert report.to_dict()["dual_gate_pass"] is True


def test_negative_control_tall_inductor_collides_with_rib():
    # The canonical negative control: a 6.5 mm inductor over a 5.3 mm rib.
    case = make_pcba_enclosure_case(collide_tall_component=True)
    report = grade_pcba_enclosure(
        case["components"],
        case["keepouts"],
        case["enclosure"],
        connectors=case["connectors"],
        cutouts=case["cutouts"],
    )
    assert report.dual_gate_pass is False
    assert report.keepout_clearance_pass is False
    assert len(report.collisions) == 1
    collision = report.collisions[0]
    assert collision["component"] == "L1"
    assert collision["keepout"] == "rib"
    assert collision["z_overlap_mm"] == pytest.approx(3.7)


def test_negative_control_misaligned_connector_fails_only_connector_gate():
    case = make_pcba_enclosure_case(misalign_connector_mm=3.0)
    report = grade_pcba_enclosure(
        case["components"],
        case["keepouts"],
        case["enclosure"],
        connectors=case["connectors"],
        cutouts=case["cutouts"],
    )
    assert report.z_height_clearance_pass is True
    assert report.keepout_clearance_pass is True
    assert report.connector_cutout_pass is False
    assert report.dual_gate_pass is False


def test_grader_without_connectors_keeps_connector_gate_vacuously_true():
    case = make_pcba_enclosure_case()
    report = grade_pcba_enclosure(case["components"], case["keepouts"], case["enclosure"])
    assert report.connector_cutout_pass is True
    assert report.connector_deltas == []
    assert report.dual_gate_pass is True
