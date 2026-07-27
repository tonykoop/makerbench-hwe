"""Optional-local `simulation-fea` profile contract tests (mb#50).

These cover the pure deterministic grader, the typed-requirement validation, and
the solver-gated availability/smoke paths — without needing gmsh or CalculiX, the
same way test_brep_profile exercises the no-build123d path.
"""

from __future__ import annotations

import importlib.util
import shutil

import pytest

from makerbench.schema import FailureLevel
from makerbench.simulation_fea import (
    PROFILE_NAME,
    REQUIREMENT_METRIC,
    FeaLoadCase,
    FeaRequirement,
    SimulationRequirementSet,
    grade_fea_requirements,
    grade_fea_smoke,
    solve_requirement_set,
    solver_availability,
)


def _reqset() -> SimulationRequirementSet:
    return SimulationRequirementSet(
        task_id="cantilever_bracket",
        load_cases=[
            FeaLoadCase(name="tip_load", analysis="static", parameters={"force_n": 500.0}),
            FeaLoadCase(name="column", analysis="buckle", parameters={"force_n": 2000.0}),
        ],
        requirements=[
            FeaRequirement(requirement_type="max_von_mises_stress_mpa", load_case="tip_load", limit=250.0),
            FeaRequirement(requirement_type="max_displacement_mm", load_case="tip_load", limit=2.0),
            FeaRequirement(requirement_type="min_buckling_factor", load_case="column", limit=1.5),
        ],
    )


# --------------------------------------------------------------------------- #
# Pure deterministic grader                                                    #
# --------------------------------------------------------------------------- #


def test_all_requirements_met():
    measured = {
        "tip_load": {"von_mises_stress_mpa": 180.0, "displacement_mm": 1.1},
        "column": {"buckling_factor": 2.3},
    }
    grade = grade_fea_requirements(measured, _reqset())
    assert grade.passed is True
    assert (grade.n_pass, grade.n_total) == (3, 3)
    assert grade.profile == PROFILE_NAME


def test_max_requirement_exceeded_fails():
    measured = {
        "tip_load": {"von_mises_stress_mpa": 300.0, "displacement_mm": 1.1},
        "column": {"buckling_factor": 2.3},
    }
    grade = grade_fea_requirements(measured, _reqset())
    assert grade.passed is False
    stress = next(r for r in grade.results if r.requirement_type == "max_von_mises_stress_mpa")
    assert stress.passed is False
    assert stress.margin == 250.0 - 300.0  # negative slack
    assert stress.utilization == 1.2  # 300/250


def test_min_requirement_below_bound_fails():
    measured = {
        "tip_load": {"von_mises_stress_mpa": 180.0, "displacement_mm": 1.1},
        "column": {"buckling_factor": 1.2},  # below 1.5
    }
    grade = grade_fea_requirements(measured, _reqset())
    buckle = next(r for r in grade.results if r.requirement_type == "min_buckling_factor")
    assert buckle.passed is False
    assert buckle.margin == pytest.approx(1.2 - 1.5)
    assert buckle.utilization == pytest.approx(0.8)


def test_boundary_value_is_inclusive():
    # measured exactly equals the limit -> passes for both directions.
    measured = {
        "tip_load": {"von_mises_stress_mpa": 250.0, "displacement_mm": 2.0},
        "column": {"buckling_factor": 1.5},
    }
    grade = grade_fea_requirements(measured, _reqset())
    assert grade.passed is True
    for r in grade.results:
        assert r.margin == 0.0


def test_missing_measurement_fails_closed():
    measured = {
        "tip_load": {"von_mises_stress_mpa": 180.0},  # displacement absent
        "column": {"buckling_factor": 2.3},
    }
    grade = grade_fea_requirements(measured, _reqset())
    disp = next(r for r in grade.results if r.requirement_type == "max_displacement_mm")
    assert disp.passed is False
    assert disp.measured is None
    assert disp.margin is None
    assert "no measured" in disp.detail
    assert grade.passed is False


def test_empty_requirement_set_does_not_pass():
    rs = SimulationRequirementSet(task_id="empty")
    grade = grade_fea_requirements({}, rs)
    assert grade.n_total == 0
    assert grade.passed is False


def test_level_result_is_physics_and_profile_scoped():
    measured = {
        "tip_load": {"von_mises_stress_mpa": 180.0, "displacement_mm": 1.1},
        "column": {"buckling_factor": 2.3},
    }
    lr = grade_fea_requirements(measured, _reqset()).level_result()
    assert lr.level == FailureLevel.PHYSICS
    assert lr.passed is True
    assert lr.checks["min_buckling_factor@column"] is True


def test_requirement_metric_mapping_is_total():
    # Every requirement type the Literal allows has a measured-metric mapping.
    from typing import get_args

    from makerbench.simulation_fea import FeaRequirementType

    assert set(REQUIREMENT_METRIC) == set(get_args(FeaRequirementType))


# --------------------------------------------------------------------------- #
# Typed-requirement validation                                                 #
# --------------------------------------------------------------------------- #


def test_requirement_referencing_unknown_load_case_rejected():
    with pytest.raises(ValueError, match="undefined load_case"):
        SimulationRequirementSet(
            task_id="t",
            load_cases=[FeaLoadCase(name="a", analysis="static")],
            requirements=[FeaRequirement(requirement_type="max_displacement_mm", load_case="ghost", limit=1.0)],
        )


def test_analysis_type_mismatch_rejected():
    # buckling factor requires a buckle load case, not a static one.
    with pytest.raises(ValueError, match="needs a 'buckle'"):
        SimulationRequirementSet(
            task_id="t",
            load_cases=[FeaLoadCase(name="s", analysis="static")],
            requirements=[FeaRequirement(requirement_type="min_buckling_factor", load_case="s", limit=1.5)],
        )


def test_duplicate_load_case_rejected():
    with pytest.raises(ValueError, match="duplicate load_case"):
        SimulationRequirementSet(
            task_id="t",
            load_cases=[FeaLoadCase(name="s", analysis="static"), FeaLoadCase(name="s", analysis="static")],
        )


def test_requirement_direction_and_analysis_properties():
    r_max = FeaRequirement(requirement_type="max_displacement_mm", load_case="x", limit=1.0)
    r_min = FeaRequirement(requirement_type="min_safety_factor", load_case="x", limit=2.0)
    assert (r_max.direction, r_max.analysis) == ("max", "static")
    assert (r_min.direction, r_min.analysis) == ("min", "static")


# --------------------------------------------------------------------------- #
# Solver gating stays importable / skips cleanly without gmsh + CalculiX       #
# --------------------------------------------------------------------------- #


def _solver_present() -> bool:
    return importlib.util.find_spec("gmsh") is not None and shutil.which("ccx") is not None


def test_solver_availability_matches_environment():
    avail = solver_availability()
    assert avail.available is _solver_present()
    if not avail.available:
        assert avail.status == "unavailable"
        assert "optional_local" in avail.reason


def test_solve_requirement_set_unavailable_skips():
    if _solver_present():
        pytest.skip("solver installed locally; this asserts the no-solver path")
    out = solve_requirement_set("missing.inp", _reqset())
    assert out["status"] == "unavailable"
    assert out["available"] is False
    assert out["profile"] == PROFILE_NAME


def test_grade_fea_smoke_skips_without_solver():
    if _solver_present():
        pytest.skip("solver installed locally; this asserts the no-solver path")
    out = grade_fea_smoke("missing.inp", _reqset())
    assert out["status"] == "skipped"
    assert out["available"] is False
    assert out["profile"] == PROFILE_NAME
