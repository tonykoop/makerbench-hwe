"""Tests for workflow-track quarterly challenge grader primitives (#97).

Covers the seven pure grader functions in ``makerbench/workflow_challenge_graders.py``
(Track A: string_path_topology, acoustic_volume_formula, cnc_ballnose_clearance;
Track B: structural_load_case, mass_reduction, no_clearance_regression,
manufacturable_reinforcement) plus the compatibility alias
``localized_string_tension_deflection`` which lives in
``instrument_acoustics_ladder.py``.

Every test is **param-only** — no oracle, no mesh, no fixture, no network call.
All functions return a ``dict[str, float]`` with a ``feasible`` key (1.0/0.0).
"""

from __future__ import annotations

from makerbench.instrument_acoustics_ladder import localized_string_tension_deflection
from makerbench.workflow_challenge_graders import (
    acoustic_volume_formula,
    cnc_ballnose_clearance,
    manufacturable_reinforcement,
    mass_reduction,
    no_clearance_regression,
    string_path_topology,
    structural_load_case,
)


# ============================================================================
# Track A: string_path_topology
# ============================================================================


class TestStringPathTopology:
    """Tests for string_path_topology."""

    _GOOD = {
        "string_count": 21,
        "string_spacing_profile": "fan",
        "total_bridge_width_mm": 120.0,  # 21 × 4.0 = 84 mm < 120
        "min_lane_spacing_mm": 4.0,
        "lanes_overlap_declared": False,
    }

    def test_typical_good_kora(self):
        r = string_path_topology(self._GOOD)
        assert r["feasible"] == 1.0
        assert r["count_is_odd"] == 1.0
        assert r["count_in_range"] == 1.0
        assert r["spacing_profile_valid"] == 1.0
        assert r["lanes_non_overlapping"] == 1.0
        assert r["lane_width_feasible"] == 1.0

    def test_even_string_count_fails(self):
        p = {**self._GOOD, "string_count": 12}
        r = string_path_topology(p)
        assert r["feasible"] == 0.0
        assert r["count_is_odd"] == 0.0

    def test_count_below_range_fails(self):
        p = {**self._GOOD, "string_count": 7}
        r = string_path_topology(p)
        assert r["feasible"] == 0.0
        assert r["count_in_range"] == 0.0

    def test_count_above_range_fails(self):
        p = {**self._GOOD, "string_count": 25}
        r = string_path_topology(p)
        assert r["feasible"] == 0.0
        assert r["count_in_range"] == 0.0

    def test_invalid_profile_fails(self):
        p = {**self._GOOD, "string_spacing_profile": "uniform"}
        r = string_path_topology(p)
        assert r["feasible"] == 0.0
        assert r["spacing_profile_valid"] == 0.0

    def test_graduated_profile_accepted(self):
        p = {**self._GOOD, "string_spacing_profile": "graduated"}
        r = string_path_topology(p)
        assert r["spacing_profile_valid"] == 1.0
        assert r["feasible"] == 1.0

    def test_asymmetric_profile_accepted(self):
        p = {**self._GOOD, "string_spacing_profile": "asymmetric"}
        r = string_path_topology(p)
        assert r["spacing_profile_valid"] == 1.0

    def test_overlapping_lanes_fails(self):
        p = {**self._GOOD, "lanes_overlap_declared": True}
        r = string_path_topology(p)
        assert r["feasible"] == 0.0
        assert r["lanes_non_overlapping"] == 0.0

    def test_too_narrow_bridge_fails(self):
        # 21 strings × 4 mm = 84 mm; provide only 80 mm
        p = {**self._GOOD, "total_bridge_width_mm": 80.0}
        r = string_path_topology(p)
        assert r["feasible"] == 0.0
        assert r["lane_width_feasible"] == 0.0

    def test_min_valid_count_nine(self):
        p = {**self._GOOD, "string_count": 9, "total_bridge_width_mm": 50.0}
        r = string_path_topology(p)
        assert r["count_is_odd"] == 1.0
        assert r["count_in_range"] == 1.0
        assert r["feasible"] == 1.0

    def test_max_valid_count_twenty_three(self):
        p = {**self._GOOD, "string_count": 23, "total_bridge_width_mm": 100.0}
        r = string_path_topology(p)
        assert r["count_in_range"] == 1.0
        assert r["feasible"] == 1.0

    def test_return_keys_complete(self):
        r = string_path_topology(self._GOOD)
        for key in (
            "string_count", "count_is_odd", "count_in_range", "spacing_profile_valid",
            "lanes_non_overlapping", "min_required_width_mm", "total_bridge_width_mm",
            "lane_width_feasible", "feasible",
        ):
            assert key in r, f"Missing key: {key}"

    def test_min_required_width_formula(self):
        p = {**self._GOOD, "string_count": 11, "min_lane_spacing_mm": 5.0}
        r = string_path_topology(p)
        assert abs(r["min_required_width_mm"] - 55.0) < 1e-6


# ============================================================================
# Track A: acoustic_volume_formula
# ============================================================================


class TestAcousticVolumeFormula:
    """Tests for acoustic_volume_formula."""

    _GOOD = {
        "declared_volume_l": 6.0,
        "target_air_volume_l": 6.0,
        "volume_tolerance_frac": 0.10,
        "has_sound_hole": True,
        "sound_hole_count": 1,
    }

    def test_exact_volume_pass(self):
        r = acoustic_volume_formula(self._GOOD)
        assert r["feasible"] == 1.0
        assert r["volume_within_tolerance"] == 1.0
        assert r["sound_hole_present"] == 1.0

    def test_volume_within_tolerance_pass(self):
        # 5 % under the 6 L target; 10 % tolerance → should pass
        p = {**self._GOOD, "declared_volume_l": 5.7}
        r = acoustic_volume_formula(p)
        assert r["volume_within_tolerance"] == 1.0
        assert r["feasible"] == 1.0

    def test_volume_outside_tolerance_fails(self):
        # 20 % under target with 10 % tolerance → fail
        p = {**self._GOOD, "declared_volume_l": 4.5}
        r = acoustic_volume_formula(p)
        assert r["volume_within_tolerance"] == 0.0
        assert r["feasible"] == 0.0

    def test_volume_over_tolerance_fails(self):
        # 15 % over target
        p = {**self._GOOD, "declared_volume_l": 7.0}
        r = acoustic_volume_formula(p)
        assert r["volume_within_tolerance"] == 0.0
        assert r["feasible"] == 0.0

    def test_no_sound_hole_fails(self):
        p = {**self._GOOD, "has_sound_hole": False, "sound_hole_count": 0}
        r = acoustic_volume_formula(p)
        assert r["sound_hole_present"] == 0.0
        assert r["feasible"] == 0.0

    def test_small_target_volume(self):
        p = {"declared_volume_l": 2.1, "target_air_volume_l": 2.0,
             "has_sound_hole": True}
        r = acoustic_volume_formula(p)
        # 2.1 vs 2.0, tolerance 0.10 (10 % of 2.0 = 0.2), error = 0.1 → pass
        assert r["feasible"] == 1.0

    def test_large_target_volume(self):
        p = {"declared_volume_l": 12.0, "target_air_volume_l": 12.0,
             "has_sound_hole": True}
        r = acoustic_volume_formula(p)
        assert r["feasible"] == 1.0

    def test_return_keys_complete(self):
        r = acoustic_volume_formula(self._GOOD)
        for key in (
            "declared_volume_l", "target_air_volume_l", "tolerance_band_l",
            "volume_error_l", "volume_within_tolerance", "sound_hole_present", "feasible",
        ):
            assert key in r, f"Missing key: {key}"

    def test_tolerance_band_computed_correctly(self):
        p = {**self._GOOD, "target_air_volume_l": 8.0, "volume_tolerance_frac": 0.05}
        r = acoustic_volume_formula(p)
        assert abs(r["tolerance_band_l"] - 0.4) < 1e-6


# ============================================================================
# Track A: cnc_ballnose_clearance
# ============================================================================


class TestCncBallnoseClearance:
    """Tests for cnc_ballnose_clearance."""

    _GOOD = {
        "ballnose_bit_dia_mm": 6.0,
        "min_concave_radius_mm": 3.5,  # > tool_radius=3.0 → ok
        "deepest_pocket_depth_mm": 12.0,  # 12 / 6 = 2.0 < 3.0 ratio → ok
        "max_depth_to_dia_ratio": 3.0,
        "tool_access_declared": True,
    }

    def test_typical_pass(self):
        r = cnc_ballnose_clearance(self._GOOD)
        assert r["feasible"] == 1.0
        assert r["radius_ok"] == 1.0
        assert r["depth_ok"] == 1.0
        assert r["tool_access_declared"] == 1.0

    def test_radius_too_tight_fails(self):
        p = {**self._GOOD, "min_concave_radius_mm": 2.0}  # < 3.0 tool radius
        r = cnc_ballnose_clearance(p)
        assert r["radius_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_radius_exactly_at_tool_radius_passes(self):
        p = {**self._GOOD, "min_concave_radius_mm": 3.0}
        r = cnc_ballnose_clearance(p)
        assert r["radius_ok"] == 1.0

    def test_pocket_too_deep_fails(self):
        p = {**self._GOOD, "deepest_pocket_depth_mm": 25.0}  # 25/6 > 3.0
        r = cnc_ballnose_clearance(p)
        assert r["depth_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_tool_access_not_declared_fails(self):
        p = {**self._GOOD, "tool_access_declared": False}
        r = cnc_ballnose_clearance(p)
        assert r["tool_access_declared"] == 0.0
        assert r["feasible"] == 0.0

    def test_3mm_bit_constraints(self):
        # Min bit: 3 mm dia → tool_radius 1.5; min_concave 1.5 → ok; depth 9 mm → ok
        p = {
            "ballnose_bit_dia_mm": 3.0,
            "min_concave_radius_mm": 1.5,
            "deepest_pocket_depth_mm": 9.0,
        }
        r = cnc_ballnose_clearance(p)
        assert r["feasible"] == 1.0

    def test_8mm_bit_max(self):
        p = {
            "ballnose_bit_dia_mm": 8.0,
            "min_concave_radius_mm": 4.5,
            "deepest_pocket_depth_mm": 20.0,
        }
        r = cnc_ballnose_clearance(p)
        assert r["feasible"] == 1.0

    def test_return_keys_complete(self):
        r = cnc_ballnose_clearance(self._GOOD)
        for key in (
            "bit_diameter_mm", "tool_radius_mm", "min_concave_radius_mm",
            "radius_ok", "deepest_pocket_depth_mm", "max_pocket_depth_mm",
            "pocket_depth_ratio", "depth_ok", "tool_access_declared", "feasible",
        ):
            assert key in r, f"Missing key: {key}"

    def test_tool_radius_equals_half_diameter(self):
        r = cnc_ballnose_clearance({"ballnose_bit_dia_mm": 7.0, "min_concave_radius_mm": 4.0})
        assert abs(r["tool_radius_mm"] - 3.5) < 1e-9


# ============================================================================
# Track A compatibility: localized_string_tension_deflection
# ============================================================================


class TestLocalizedStringTensionDeflection:
    """Verify the compatibility alias delegates to string_tension_bridge_check."""

    _GOOD = {
        "string_count": 21,
        "material_process": "cnc_hardwood_ballnose",
        "tension_class": "medium",
        "break_angle_deg": 12,
        "bridge_span_mm": 80.0,
        "bridge_footprint_depth_mm": 30.0,
        "section_thickness_mm": 12.0,
        "load_path_declared": True,
    }

    def test_good_params_pass(self):
        r = localized_string_tension_deflection(self._GOOD)
        assert r["feasible"] == 1.0
        assert "downforce_n" in r
        assert "max_deflection_mm" in r

    def test_load_path_false_fails(self):
        p = {**self._GOOD, "load_path_declared": False}
        r = localized_string_tension_deflection(p)
        assert r["load_path_declared"] == 0.0
        assert r["feasible"] == 0.0

    def test_very_thin_section_fails(self):
        p = {**self._GOOD, "section_thickness_mm": 1.0}
        r = localized_string_tension_deflection(p)
        assert r["feasible"] == 0.0


# ============================================================================
# Track B: structural_load_case
# ============================================================================


class TestStructuralLoadCase:
    """Tests for structural_load_case."""

    _GOOD = {
        "declared_max_displacement_mm": 0.8,
        "displacement_limit_mm": 1.0,
        "declared_safety_factor": 3.0,
        "min_safety_factor": 2.0,
        "force_vector_count": 3,
        "load_path_complete": True,
    }

    def test_typical_pass(self):
        r = structural_load_case(self._GOOD)
        assert r["feasible"] == 1.0
        assert r["displacement_ok"] == 1.0
        assert r["safety_factor_ok"] == 1.0
        assert r["all_force_vectors_resolved"] == 1.0

    def test_displacement_exceeded_fails(self):
        p = {**self._GOOD, "declared_max_displacement_mm": 1.5}
        r = structural_load_case(p)
        assert r["displacement_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_safety_factor_too_low_fails(self):
        p = {**self._GOOD, "declared_safety_factor": 1.5}
        r = structural_load_case(p)
        assert r["safety_factor_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_load_path_incomplete_fails(self):
        p = {**self._GOOD, "load_path_complete": False}
        r = structural_load_case(p)
        assert r["all_force_vectors_resolved"] == 0.0
        assert r["feasible"] == 0.0

    def test_no_force_vectors_fails(self):
        p = {**self._GOOD, "force_vector_count": 0}
        r = structural_load_case(p)
        assert r["all_force_vectors_resolved"] == 0.0
        assert r["feasible"] == 0.0

    def test_exact_displacement_limit_passes(self):
        p = {**self._GOOD, "declared_max_displacement_mm": 1.0}
        r = structural_load_case(p)
        assert r["displacement_ok"] == 1.0

    def test_exact_safety_factor_passes(self):
        p = {**self._GOOD, "declared_safety_factor": 2.0}
        r = structural_load_case(p)
        assert r["safety_factor_ok"] == 1.0

    def test_return_keys_complete(self):
        r = structural_load_case(self._GOOD)
        for key in (
            "declared_max_displacement_mm", "displacement_limit_mm",
            "displacement_ok", "declared_safety_factor", "min_safety_factor",
            "safety_factor_ok", "force_vector_count", "all_force_vectors_resolved",
            "load_path_complete", "feasible",
        ):
            assert key in r, f"Missing key: {key}"


# ============================================================================
# Track B: mass_reduction
# ============================================================================


class TestMassReduction:
    """Tests for mass_reduction."""

    _GOOD = {
        "baseline_mass_g": 500.0,
        "submitted_mass_g": 400.0,  # 20 % reduction
        "target_reduction_pct": 15.0,
    }

    def test_typical_pass(self):
        r = mass_reduction(self._GOOD)
        assert r["feasible"] == 1.0
        assert r["mass_reduced"] == 1.0
        assert abs(r["actual_reduction_pct"] - 20.0) < 1e-6

    def test_insufficient_reduction_fails(self):
        # Only 10 % reduction, target is 15 %
        p = {**self._GOOD, "submitted_mass_g": 450.0}
        r = mass_reduction(p)
        assert r["mass_reduced"] == 0.0
        assert r["feasible"] == 0.0

    def test_heavier_submission_fails(self):
        p = {**self._GOOD, "submitted_mass_g": 550.0}
        r = mass_reduction(p)
        assert r["mass_reduced"] == 0.0
        assert r["feasible"] == 0.0

    def test_zero_submitted_mass_fails(self):
        p = {**self._GOOD, "submitted_mass_g": 0.0}
        r = mass_reduction(p)
        assert r["mass_reduced"] == 0.0
        assert r["feasible"] == 0.0

    def test_exact_target_pct_passes(self):
        # Exactly 15 % reduction
        p = {**self._GOOD, "submitted_mass_g": 425.0}
        r = mass_reduction(p)
        assert r["mass_reduced"] == 1.0
        assert r["feasible"] == 1.0

    def test_reduction_formula_correct(self):
        p = {**self._GOOD, "baseline_mass_g": 200.0, "submitted_mass_g": 160.0}
        r = mass_reduction(p)
        assert abs(r["actual_reduction_pct"] - 20.0) < 1e-6
        assert abs(r["absolute_reduction_g"] - 40.0) < 1e-6

    def test_return_keys_complete(self):
        r = mass_reduction(self._GOOD)
        for key in (
            "baseline_mass_g", "submitted_mass_g", "absolute_reduction_g",
            "actual_reduction_pct", "target_reduction_pct", "mass_reduced", "feasible",
        ):
            assert key in r, f"Missing key: {key}"


# ============================================================================
# Track B: no_clearance_regression
# ============================================================================


class TestNoClearanceRegression:
    """Tests for no_clearance_regression."""

    _GOOD = {
        "interference_volume_mm3": 0.0,
        "keepout_zone_count": 3,
        "keepout_zones_checked": 3,
        "fastener_clearance_ok": True,
        "link_clearance_ok": True,
    }

    def test_zero_interference_pass(self):
        r = no_clearance_regression(self._GOOD)
        assert r["feasible"] == 1.0
        assert r["zero_interference"] == 1.0
        assert r["all_keepouts_clear"] == 1.0

    def test_nonzero_interference_fails(self):
        p = {**self._GOOD, "interference_volume_mm3": 0.5}
        r = no_clearance_regression(p)
        assert r["zero_interference"] == 0.0
        assert r["feasible"] == 0.0

    def test_unchecked_keepout_fails(self):
        p = {**self._GOOD, "keepout_zones_checked": 2}
        r = no_clearance_regression(p)
        assert r["all_keepouts_clear"] == 0.0
        assert r["feasible"] == 0.0

    def test_fastener_clearance_fail(self):
        p = {**self._GOOD, "fastener_clearance_ok": False}
        r = no_clearance_regression(p)
        assert r["fastener_clearance_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_link_clearance_fail(self):
        p = {**self._GOOD, "link_clearance_ok": False}
        r = no_clearance_regression(p)
        assert r["link_clearance_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_near_zero_interference_treated_as_zero(self):
        # 1e-12 mm³ is essentially numerical noise — should still pass
        p = {**self._GOOD, "interference_volume_mm3": 1e-12}
        r = no_clearance_regression(p)
        assert r["zero_interference"] == 1.0

    def test_return_keys_complete(self):
        r = no_clearance_regression(self._GOOD)
        for key in (
            "interference_volume_mm3", "zero_interference", "keepout_zone_count",
            "keepout_zones_checked", "all_keepouts_clear",
            "fastener_clearance_ok", "link_clearance_ok", "feasible",
        ):
            assert key in r, f"Missing key: {key}"


# ============================================================================
# Track B: manufacturable_reinforcement
# ============================================================================


class TestManufacturableReinforcement:
    """Tests for manufacturable_reinforcement."""

    _GOOD_CNC = {
        "manufacturing_process": "cnc_aluminum",
        "min_declared_rib_thickness_mm": 2.0,   # > 1.5 min
        "min_declared_fillet_radius_mm": 1.0,   # > 0.5 min
        "tool_access_ok": True,
    }
    _GOOD_FDM = {
        "manufacturing_process": "fdm_cf_nylon",
        "min_declared_rib_thickness_mm": 1.5,   # > 1.2 min
        "min_declared_fillet_radius_mm": 0.0,   # FDM tolerates 0
        "tool_access_ok": True,
    }
    _GOOD_SLS = {
        "manufacturing_process": "sls_nylon",
        "min_declared_rib_thickness_mm": 1.0,   # > 0.8 min
        "min_declared_fillet_radius_mm": 0.0,
        "tool_access_ok": True,
    }

    def test_cnc_aluminum_pass(self):
        r = manufacturable_reinforcement(self._GOOD_CNC)
        assert r["feasible"] == 1.0

    def test_fdm_cf_nylon_pass(self):
        r = manufacturable_reinforcement(self._GOOD_FDM)
        assert r["feasible"] == 1.0

    def test_sls_nylon_pass(self):
        r = manufacturable_reinforcement(self._GOOD_SLS)
        assert r["feasible"] == 1.0

    def test_cnc_rib_too_thin_fails(self):
        p = {**self._GOOD_CNC, "min_declared_rib_thickness_mm": 1.0}  # < 1.5
        r = manufacturable_reinforcement(p)
        assert r["rib_thickness_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_cnc_fillet_too_small_fails(self):
        # CNC aluminum requires min_fillet 0.5; supply 0.2
        p = {**self._GOOD_CNC, "min_declared_fillet_radius_mm": 0.2}
        r = manufacturable_reinforcement(p)
        assert r["fillet_radius_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_tool_access_blocked_fails(self):
        p = {**self._GOOD_CNC, "tool_access_ok": False}
        r = manufacturable_reinforcement(p)
        assert r["tool_access_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_fdm_allows_zero_fillet(self):
        # FDM process: min_fillet = 0, so 0.0 passes
        r = manufacturable_reinforcement(self._GOOD_FDM)
        assert r["fillet_radius_ok"] == 1.0

    def test_sls_rib_too_thin_fails(self):
        p = {**self._GOOD_SLS, "min_declared_rib_thickness_mm": 0.5}  # < 0.8
        r = manufacturable_reinforcement(p)
        assert r["rib_thickness_ok"] == 0.0
        assert r["feasible"] == 0.0

    def test_explicit_override_respected(self):
        # Explicit min_rib_mm=2.0 should override the process table
        p = {**self._GOOD_CNC, "min_rib_mm": 2.0, "min_declared_rib_thickness_mm": 1.8}
        r = manufacturable_reinforcement(p)
        assert r["process_min_rib_mm"] == 2.0
        assert r["rib_thickness_ok"] == 0.0  # 1.8 < 2.0

    def test_return_keys_complete(self):
        r = manufacturable_reinforcement(self._GOOD_CNC)
        for key in (
            "manufacturing_process", "min_declared_rib_thickness_mm",
            "process_min_rib_mm", "rib_thickness_ok",
            "min_declared_fillet_radius_mm", "process_min_fillet_mm",
            "fillet_radius_ok", "tool_access_ok", "feasible",
        ):
            assert key in r, f"Missing key: {key}"

    def test_process_name_preserved_in_return(self):
        r = manufacturable_reinforcement(self._GOOD_CNC)
        assert r["manufacturing_process"] == "cnc_aluminum"
