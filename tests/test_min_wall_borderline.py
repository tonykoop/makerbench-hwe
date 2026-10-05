"""#1011 (Tony, 2026-10-04): a robust-v1 min_wall on the 1st-percentile cliff is "borderline".

When 0.8-1.2 % of the 20,000 wall samples lie below the threshold, the verdict is "borderline":
excluded from the objective pass rate (that trial's denominator drops the check), neither a pass
nor a fail. The share is always reported under robust-v1. Legacy "min" is unchanged.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from makerbench import code_cad_arena_report as report
from makerbench import code_cad_arena_runner as runner
from makerbench import geometry
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts, _normalize_gate_result

SPEC = {"id": "x", "envelope_mm": [200, 200, 200], "min_bodies": 1, "min_wall_mm": 1.0}


def _bladed(width: float) -> trimesh.Trimesh:
    """A 50 x 50 x 5 mm plate with a 0.4 mm blade (6 mm deep) sticking out ``width`` mm: the
    blade is the only wall under the 1 mm floor, and its width sets the share of samples on it.
    Measured (fixed seed, canonical layout): 4 mm -> 0.655 %, 5.5 mm -> 1.000 %, 7 mm -> 1.32 %."""
    plate = trimesh.creation.box(extents=[50, 50, 5])
    blade = trimesh.creation.box(extents=[width, 6.0, 0.4])
    blade.apply_translation([25 + width / 2 - 0.5, 0, 0])
    return trimesh.boolean.union([plate, blade], engine="manifold")


@pytest.fixture(scope="module")
def cliff():
    return _bladed(5.5)


def _gate(tmp_path, mesh, **kw):
    stl = tmp_path / "output.stl"
    mesh.export(stl.as_posix())
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    ctx = ObjectiveContext(trial_id="t", model_id="m", instrument_id="x", seed=0,
                           scad_path=tmp_path / "x.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    return runner.mesh_objective_gate(SPEC, part_module_counter=lambda _p: 0, **kw)(ctx)


def test_share_is_the_samples_failing_the_verdicts_own_comparison(cliff):
    out = geometry.estimate_wall_robust_v1(cliff, floor_mm=1.0)
    dists = geometry._wall_distances(geometry.canonical_mesh(cliff), geometry.ROBUST_V1_SAMPLES,
                                     geometry.ROBUST_V1_SEED)
    expected = int(np.count_nonzero(dists < 1.0 - geometry.WALL_MEAS_TOL_MM))
    assert out["below_floor"] == expected and out["n_samples"] == len(dists)
    assert out["below_floor_share"] == expected / len(dists)
    # without a floor the estimator's committed shape is unchanged
    assert set(geometry.estimate_wall_robust_v1(cliff)) == {"wall_mm", "min_mm", "n_samples"}


@pytest.mark.parametrize("below, borderline", [(158, False), (160, True), (200, True), (240, True), (242, False)],
                         ids=["0.79%", "0.80%", "1.00%", "1.20%", "1.21%"])
def test_band_edges_are_inclusive_and_exact(below, borderline):
    assert geometry.is_borderline_share(below, 20000) is borderline
    assert geometry.is_borderline_share(0, 0) is False


@pytest.mark.parametrize("width, verdict", [(4.0, 1.0), (5.5, "borderline"), (7.0, 0.0)])
def test_a_mesh_on_the_cliff_is_borderline_and_off_it_is_firm(tmp_path, width, verdict):
    result = _gate(tmp_path, _bladed(width))
    assert result["sub_scores"]["min_wall"] == verdict
    share = result["metrics"]["min_wall_below_floor_share"]
    assert share is not None and (0.008 <= share <= 0.012) is (verdict == "borderline")


def test_borderline_leaves_the_denominator(tmp_path, cliff):
    result = _gate(tmp_path, cliff)
    sub = result["sub_scores"]
    assert sub["min_wall"] == "borderline"
    decided = [v for v in sub.values() if v != "borderline"]
    assert result["objective_pass_rate"] == pytest.approx(sum(decided) / len(decided))
    assert len(decided) == len(sub) - 1
    assert result["objective_pass_rate"] == 1.0 and result["passed"] is True  # every decided check passes
    assert not [f for f in result["failures"] if f["check"] == "min_wall"]  # not a failure either
    assert runner.objective_rate({"a": 1.0, "b": 0.0, "min_wall": "borderline"}) == 0.5


def test_borderline_flows_through_persistence_scoreline_and_reports(tmp_path, cliff):
    objective = _normalize_gate_result(_gate(tmp_path, cliff))
    assert objective["sub_scores"]["min_wall"] == "borderline"
    assert objective["min_wall_below_floor_share"] == pytest.approx(0.01, abs=1e-6)
    passing = {**objective, "sub_scores": {**objective["sub_scores"], "min_wall": 1.0}}
    log = {"trials": [
        {"trial_id": "t1", "model_id": "m", "instrument_id": "x", "seed": 0, "status": "scored",
         "result": {"objective": objective}},
        {"trial_id": "t2", "model_id": "m", "instrument_id": "x", "seed": 1, "status": "scored",
         "result": {"objective": passing}}]}
    (row,) = runner.collect_objective_scoreline(log)
    assert row["objective_pass_rate"] == 1.0  # the borderline trial counts its decided checks only
    (item,) = row["borderline_checks"]
    assert item["trial_id"] == "t1" and item["check"] == "min_wall"
    assert item["below_floor_share"] == pytest.approx(0.01, abs=1e-6)
    assert "failed_checks" not in row
    means = report.gate_means(log)["m"]
    assert means["min_wall"] == 1.0  # one decided trial (t2), not 0.5
    assert report.gate_means({"trials": log["trials"][:1]})["m"]["min_wall"] is None
    assert "–" in report._gate_matrix({"m": {**means, "min_wall": None}})
    json.dumps(row)  # serialisable as written to objective_scoreline.json


def test_legacy_min_is_unchanged(tmp_path, cliff):
    legacy = _gate(tmp_path, cliff, min_wall_estimator="min")
    assert legacy["min_wall_method"] == "min"
    assert legacy["sub_scores"]["min_wall"] in (0.0, 1.0)
    assert "min_wall_below_floor_share" not in legacy["metrics"]
    assert "min_wall_below_floor_share" not in _normalize_gate_result(legacy)
    assert legacy["objective_pass_rate"] == sum(legacy["sub_scores"].values()) / len(legacy["sub_scores"])


def test_share_is_always_reported_under_robust_v1(tmp_path):
    firm = _gate(tmp_path, _bladed(4.0))
    assert firm["metrics"]["min_wall_below_floor_share"] == pytest.approx(0.00655, abs=1e-5)
    assert _normalize_gate_result(firm)["min_wall_below_floor_share"] == firm["metrics"]["min_wall_below_floor_share"]
    failing = _gate(tmp_path, _bladed(7.0))
    (failure,) = [f for f in failing["failures"] if f["check"] == "min_wall"]
    assert failure["below_floor_share"] == pytest.approx(0.0132, abs=1e-4)
    assert "of the samples are below the threshold" in failure["detail"]
