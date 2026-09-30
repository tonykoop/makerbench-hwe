"""min_wall "robust-v1" (#901): opt-in, seed-stable, and the default is untouched."""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import numpy as np
import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import geometry
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts

SPEC = {"id": "x", "envelope_mm": [200, 200, 200], "min_bodies": 1, "min_wall_mm": 1.0}


@pytest.fixture(scope="module")
def plate_with_a_tiny_blade() -> trimesh.Trimesh:
    """A 50x50x5 plate with a 1 mm-wide, 0.3 mm-thick blade sticking out of one side: a real
    thin feature on ~0.03% of the surface, the kind the minimum finds only if a sample lands on it."""
    plate = trimesh.creation.box(extents=[50, 50, 5])
    blade = trimesh.creation.box(extents=[1, 1, 0.3])
    blade.apply_translation([25, 0, 0])
    mesh = trimesh.boolean.union([plate, blade], engine="manifold")
    assert mesh.is_watertight and len(mesh.split(only_watertight=False)) == 1
    return mesh


def test_default_minimum_flips_with_the_sample_seed(plate_with_a_tiny_blade):
    verdicts = {geometry.printable_wall(geometry.estimate_min_wall_mm(plate_with_a_tiny_blade, 4000, seed=s), 1.0)
                for s in range(10)}
    assert verdicts == {True, False}  # the problem #901 exists to fix


def test_robust_v1_is_deterministic_and_reports_the_raw_minimum(plate_with_a_tiny_blade):
    a = geometry.estimate_wall_robust_v1(plate_with_a_tiny_blade)
    b = geometry.estimate_wall_robust_v1(plate_with_a_tiny_blade)
    assert a == b and a["n_samples"] > 15000
    assert a["wall_mm"] >= 4.9          # the 1st percentile sees a 5 mm plate
    assert a["min_mm"] <= a["wall_mm"]  # the raw minimum is reported alongside


def test_robust_v1_still_fails_a_genuinely_thin_part():
    sheet = trimesh.creation.box(extents=[50, 50, 0.4])
    assert geometry.estimate_wall_robust_v1(sheet)["wall_mm"] == pytest.approx(0.4, abs=0.05)


def test_robust_v1_open_mesh_and_no_hit_conventions():
    box = trimesh.creation.box(extents=[10, 10, 10])
    box.update_faces([False] + [True] * (len(box.faces) - 1))
    box.remove_unreferenced_vertices()
    assert geometry.estimate_wall_robust_v1(box)["wall_mm"] == 0.0


def _payload(tmp_path, mesh, **kw):
    stl = tmp_path / "output.stl"
    mesh.export(stl.as_posix())
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    ctx = ObjectiveContext(trial_id="t", model_id="m", instrument_id="i", seed=0,
                           scad_path=tmp_path / "x.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    spec = kw.pop("spec", SPEC)
    return runner.mesh_objective_gate(spec, **kw)(ctx)


def test_default_gate_is_unchanged_and_carries_no_method_marker(tmp_path, plate_with_a_tiny_blade):
    default = _payload(tmp_path, plate_with_a_tiny_blade)
    explicit = _payload(tmp_path, plate_with_a_tiny_blade, min_wall_estimator="min")
    assert default == explicit
    assert "min_wall_method" not in default["metrics"]


def test_robust_gate_passes_the_blade_plate_and_marks_the_method(tmp_path, plate_with_a_tiny_blade):
    result = _payload(tmp_path, plate_with_a_tiny_blade, min_wall_estimator="robust-v1")
    assert result["sub_scores"]["min_wall"] == 1.0 and result["metrics"]["min_wall_method"] == "robust-v1"


def test_robust_gate_can_be_selected_from_the_spec(tmp_path, plate_with_a_tiny_blade):
    result = _payload(tmp_path, plate_with_a_tiny_blade, spec={**SPEC, "min_wall_estimator": "robust-v1"})
    assert result["metrics"]["min_wall_method"] == "robust-v1"


def test_robust_failure_explanation_names_the_method_and_the_raw_minimum(tmp_path):
    result = _payload(tmp_path, trimesh.creation.box(extents=[50, 50, 0.4]), min_wall_estimator="robust-v1")
    (fail,) = [f for f in result["failures"] if f["check"] == "min_wall"]
    assert fail["method"] == "robust-v1" and "1st percentile" in fail["detail"] and "raw minimum" in fail["detail"]
    assert fail["measured"] == pytest.approx(0.4, abs=0.05) and fail["threshold"] == 1.0


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError, match="min_wall_estimator"):
        runner.mesh_objective_gate(SPEC, min_wall_estimator="p1-experimental")


def test_estimate_min_wall_mm_is_unchanged_by_sharing_the_sampling_code(plate_with_a_tiny_blade):
    # Values recorded from the estimator BEFORE the sampling code was factored out (4000 samples,
    # seeds 0-9), so the default behaviour, and every committed result, is provably the same.
    got = [round(geometry.estimate_min_wall_mm(plate_with_a_tiny_blade, 4000, seed=s), 2) for s in range(10)]
    assert got == [5.0, 5.0, 1.0, 0.3, 5.0, 0.3, 5.0, 5.0, 5.0, 5.0]


# --- the 13 measured sambuca meshes (not committed: they live in gitignored run dirs) ---

REAL = os.environ.get("MAKERBENCH_SAMBUCA_RUN_GLOB")


@pytest.mark.skipif(not REAL, reason="set MAKERBENCH_SAMBUCA_RUN_GLOB to the sambuca run_log.json glob to run")
def test_robust_v1_is_seed_stable_on_the_measured_sambuca_meshes():
    """Local evidence run (see docs/showcase/strings/min-wall-analysis.md): for each mesh with a
    watertight body, the robust verdict is identical for sample seeds 0-9 (default: flips)."""
    checked = flips_default = 0
    for log_path in sorted(glob.glob(REAL, recursive=True)):
        base = log_path.rsplit("/runs/", 1)[0]
        for trial in json.loads(Path(log_path).read_text())["trials"]:
            result = trial.get("result") or {}
            stl = (result.get("artifacts") or {}).get("stl_path")
            if not stl or not trial["trial_id"].startswith("sambuca"):
                continue
            mesh = trimesh.load(stl if stl.startswith("/") else f"{base}/{stl}", force="mesh")
            keep = mesh.nondegenerate_faces(height=1e-6)
            mesh.update_faces(keep)
            mesh.remove_unreferenced_vertices()
            solids = [b for b in mesh.split(only_watertight=False) if b.is_watertight]
            if not solids:
                continue
            body = max(solids, key=lambda b: len(b.faces))
            robust = {}
            for seed in range(10):
                pts, fi = trimesh.sample.sample_surface(body, geometry.ROBUST_V1_SAMPLES, seed=seed)
                n = body.face_normals[fi]
                org = pts - n * 1e-3
                loc, idx, _ = body.ray.intersects_location(org, -n, multiple_hits=False)
                d = np.linalg.norm(loc - org[idx], axis=1)
                robust[seed] = geometry.printable_wall(float(np.percentile(d[d > 1e-4], geometry.ROBUST_V1_PERCENTILE)), 1.0)
            assert len(set(robust.values())) == 1, f"{trial['trial_id']}: robust verdict flips with the seed"
            fixed = geometry.estimate_wall_robust_v1(body)
            assert geometry.printable_wall(fixed["wall_mm"], 1.0) == robust[0]
            flips_default += len({geometry.printable_wall(geometry.estimate_min_wall_mm(body, 4000, seed=s), 1.0)
                                  for s in range(10)}) > 1
            checked += 1
    assert checked >= 13
    assert flips_default >= 10  # the default flips on nearly all of them
