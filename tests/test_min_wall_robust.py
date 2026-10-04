"""min_wall "robust-v1" (#901): seed-stable, the default since epic T2; the legacy "min" stays selectable."""
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


def test_default_gate_is_robust_v1_and_marks_the_method(tmp_path, plate_with_a_tiny_blade):
    default = _payload(tmp_path, plate_with_a_tiny_blade)
    explicit = _payload(tmp_path, plate_with_a_tiny_blade, min_wall_estimator="robust-v1")
    assert default == explicit
    assert default["min_wall_method"] == default["metrics"]["min_wall_method"] == "robust-v1"
    assert geometry.MIN_WALL_METHOD_DEFAULT == "robust-v1"


def test_legacy_min_is_still_selectable_and_marked(tmp_path, plate_with_a_tiny_blade):
    legacy = _payload(tmp_path, plate_with_a_tiny_blade, min_wall_estimator="min")
    from_spec = _payload(tmp_path, plate_with_a_tiny_blade, spec={**SPEC, "min_wall_estimator": "min"})
    assert legacy == from_spec
    assert legacy["min_wall_method"] == legacy["metrics"]["min_wall_method"] == "min"
    # the legacy number is the old estimator's: minimum over 4,000 samples at seed 0
    body = plate_with_a_tiny_blade
    assert legacy["metrics"]["min_wall_mm"] == round(geometry.estimate_min_wall_mm(body, seed=0), 4)


def test_override_beats_the_spec(tmp_path, plate_with_a_tiny_blade):
    result = _payload(tmp_path, plate_with_a_tiny_blade, spec={**SPEC, "min_wall_estimator": "robust-v1"},
                      min_wall_estimator="min")
    assert result["min_wall_method"] == "min"


def test_robust_gate_passes_the_blade_plate_and_marks_the_method(tmp_path, plate_with_a_tiny_blade):
    result = _payload(tmp_path, plate_with_a_tiny_blade, min_wall_estimator="robust-v1")
    assert result["sub_scores"]["min_wall"] == 1.0 and result["metrics"]["min_wall_method"] == "robust-v1"


def test_robust_gate_can_be_selected_from_the_spec(tmp_path, plate_with_a_tiny_blade):
    result = _payload(tmp_path, plate_with_a_tiny_blade, spec={**SPEC, "min_wall_estimator": "robust-v1"})
    assert result["metrics"]["min_wall_method"] == "robust-v1"


def test_legacy_failure_explanation_keeps_its_committed_shape(tmp_path):
    result = _payload(tmp_path, trimesh.creation.box(extents=[50, 50, 0.4]), min_wall_estimator="min")
    (fail,) = [f for f in result["failures"] if f["check"] == "min_wall"]
    assert "method" not in fail and "thinnest ray-cast wall" in fail["detail"]
    assert result["metrics"]["min_wall_method"] == "min"  # the result itself still names it


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


# --- the estimator policy survives the persisted objective and the scoreline (#918 review) ---

def _persisted(tmp_path, mesh, method):
    """The real evaluate_objective_trial path, not the bare gate."""
    from makerbench.code_cad_objective import evaluate_objective_trial

    stl = tmp_path / "output.stl"
    mesh.export(stl.as_posix())
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    return evaluate_objective_trial(
        trial_id="t", model_id="m", instrument_id="i", seed=0, scad_path=tmp_path / "x.scad",
        out_dir=tmp_path / "out", objective_gate=runner.mesh_objective_gate(SPEC, min_wall_estimator=method),
        compiler=lambda _src, _out: RenderArtifacts(stl_path=stl, png_path=png),
    )


@pytest.mark.parametrize("mesh_kind", ["passing", "failing"])
def test_persisted_objective_always_records_the_method(tmp_path, mesh_kind):
    mesh = trimesh.creation.box(extents=[50, 50, 5 if mesh_kind == "passing" else 0.4])
    (tmp_path / "d").mkdir()
    (tmp_path / "l").mkdir()
    default = _persisted(tmp_path / "d", mesh, None)
    legacy = _persisted(tmp_path / "l", mesh, "min")
    assert default["objective"]["min_wall_method"] == "robust-v1"  # durable, even when it passes
    assert legacy["objective"]["min_wall_method"] == "min"


def test_scoreline_never_mixes_estimator_policies_in_one_row(tmp_path):
    mesh = trimesh.creation.box(extents=[50, 50, 5])
    (tmp_path / "l").mkdir()
    (tmp_path / "r").mkdir()
    legacy = _persisted(tmp_path / "l", mesh, "min")
    robust = _persisted(tmp_path / "r", mesh, None)  # the default
    # a result persisted before the marker existed (every committed bundle) was scored with "min"
    unversioned = {**legacy, "objective": {k: v for k, v in legacy["objective"].items() if k != "min_wall_method"}}

    def trial(payload, tid):
        return {"trial_id": tid, "model_id": "same-entrant", "instrument_id": "i", "seed": 0,
                "status": "scored", "result": payload}

    rows = runner.collect_objective_scoreline({"config": {"backend": "openscad"},
                                                "trials": [trial(legacy, "a"), trial(robust, "b"),
                                                           trial(unversioned, "c")]})
    assert len(rows) == 2
    by_method = {r.get("min_wall_method", "min"): r for r in rows}
    assert set(by_method) == {"min", "robust-v1"}
    assert "min_wall_method" not in by_method["min"] and by_method["min"]["n_objective_trials"] == 2
    assert by_method["robust-v1"]["n_objective_trials"] == 1
    # legacy-only logs keep the exact committed row shape
    (row,) = runner.collect_objective_scoreline({"trials": [trial(unversioned, "a")]})
    assert set(row) == {"entrant", "backend", "objective_pass_rate", "n_objective_trials"}


# --- real pipeline: failures before scoring, and the public boundary (#918 review, round 2) ---

ROBUST_REGISTRY = {"instruments": [{"id": "boxolin", "task_brief": "a box instrument", "envelope_mm": [200, 200, 200],
                                    "min_bodies": 1, "min_wall_estimator": "robust-v1"}]}


def _fake_box_compiler(**_kw):
    def compiler(scad_path, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        stl = out_dir / "output.stl"
        trimesh.creation.box(extents=[30, 30, 30]).export(stl.as_posix())
        png = out_dir / "preview.png"
        png.write_bytes(b"\x89PNG\r\n")
        return RenderArtifacts(stl_path=stl, png_path=png)

    return compiler


def _run(tmp_path, registry, generator, compiler):
    from makerbench.code_cad_orchestrator import OrchestrationConfig, run_orchestration

    config = OrchestrationConfig(instrument_ids=("boxolin",), model_ids=("stub-a",), seeds=(0, 1), reps=1,
                                 max_attempts=1)
    execute = runner.make_execute_trial(backend="openscad", registry=registry, run_dir=tmp_path,
                                        generators={"stub-a": generator}, compiler=compiler)
    return run_orchestration(config=config, run_log_path=tmp_path / "run_log.json", execute_trial=execute)


def _flaky_generator(fail_seed):
    from makerbench.code_cad_providers import make_stub_generator

    inner = make_stub_generator()

    def generate(request):
        if request.seed == fail_seed:
            raise RuntimeError("generation failed")
        return inner(request)

    return generate


def test_generation_failure_stays_in_the_configured_policy_row(tmp_path):
    log = _run(tmp_path, ROBUST_REGISTRY, _flaky_generator(0), _fake_box_compiler())
    failed = next(e for e in log["trials"] if e["status"] == "error")
    assert failed["meta"]["min_wall_method"] == "robust-v1"
    rows = runner.collect_objective_scoreline(log)
    assert len(rows) == 1, rows                       # no untagged default row was invented
    (row,) = rows
    assert row["min_wall_method"] == "robust-v1" and row["n_objective_trials"] == 2
    assert row["objective_pass_rate"] == pytest.approx(0.5)  # the failure still counts as zero


def test_compile_failure_stays_in_the_configured_policy_row(tmp_path):
    from makerbench import render

    def failing_compiler(scad_path, out_dir):
        raise render.CompileError("openscad exploded")

    log = _run(tmp_path, ROBUST_REGISTRY, _flaky_generator(-1), failing_compiler)
    assert {e["status"] for e in log["trials"]} == {"auto_fail"}
    rows = runner.collect_objective_scoreline(log)
    assert len(rows) == 1 and rows[0]["min_wall_method"] == "robust-v1" and rows[0]["objective_pass_rate"] == 0.0


def test_default_registry_failures_stay_in_the_robust_row(tmp_path):
    registry = {"instruments": [{k: v for k, v in ROBUST_REGISTRY["instruments"][0].items() if k != "min_wall_estimator"}]}
    log = _run(tmp_path, registry, _flaky_generator(0), _fake_box_compiler())
    failed = next(e for e in log["trials"] if e["status"] == "error")
    assert failed["meta"]["min_wall_method"] == "robust-v1"
    (row,) = runner.collect_objective_scoreline(log)
    assert row["min_wall_method"] == "robust-v1" and row["n_objective_trials"] == 2


def test_legacy_registry_failures_keep_the_legacy_row_shape(tmp_path):
    registry = {"instruments": [{**ROBUST_REGISTRY["instruments"][0], "min_wall_estimator": "min"}]}
    log = _run(tmp_path, registry, _flaky_generator(0), _fake_box_compiler())
    failed = next(e for e in log["trials"] if e["status"] == "error")
    assert failed["meta"]["min_wall_method"] == "min"
    (row,) = runner.collect_objective_scoreline(log)
    assert "min_wall_method" not in row and row["n_objective_trials"] == 2


def _build_data():
    import importlib.util

    spec = importlib.util.spec_from_file_location("mb_site_build_data_901", ROOT_DIR / "site" / "build_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT_DIR = Path(__file__).resolve().parent.parent


def test_public_pages_withhold_robust_policy_rows(tmp_path):
    """Native scoreline -> the real publishers: a robust row is never shown unlabelled next to the
    legacy rows every published round was scored with (labelling it is a follow-up)."""
    (tmp_path / "robust").mkdir()
    log = _run(tmp_path / "robust", ROBUST_REGISTRY, _flaky_generator(-1), _fake_box_compiler())
    robust_rows = runner.collect_objective_scoreline(log)
    default_rows = [{"entrant": "claude-code-sonnet", "backend": "openscad", "objective_pass_rate": 0.5,
                     "n_objective_trials": 2, "confinement": "verified"}]
    assert robust_rows[0]["min_wall_method"] == "robust-v1"

    build_data = _build_data()
    round_dir = tmp_path / "runs" / "code_cad_arena" / "round2"
    round_dir.mkdir(parents=True)
    (round_dir / "objective_scoreline.json").write_text(
        json.dumps({"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": robust_rows + default_rows}),
        encoding="utf-8")
    page = build_data.build_arena_page(tmp_path / "runs")
    assert [r["entrant"] for r in page["rounds"][0]["scoreline"]] == ["claude-code-sonnet"]  # robust row withheld

    # only-robust round: nothing to publish at all
    (round_dir / "objective_scoreline.json").write_text(
        json.dumps({"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": robust_rows}), encoding="utf-8")
    assert build_data.build_arena_page(tmp_path / "runs") is None

    entry = build_data._arena_run_entry("r", {"scoreline": robust_rows + default_rows,
                                              "run_log": {"config": {"model_ids": ["claude-code-sonnet"]}}})
    assert [r["entrant"] for r in entry["objective_pass_rate"]] == ["claude-code-sonnet"]


@pytest.mark.parametrize("samples, seed", [(4000, 0), (4000, 7), (20000, 0)])
def test_batched_wall_ray_cast_is_identical_to_one_cast(monkeypatch, samples, seed):
    """#997: wall rays are cast in batches (a single 20,000-ray robust-v1 cast on a large
    mesh peaked at ~18 GB and killed CI runners). Batching must not change a single value
    or the order the hits come back in (min_wall_sample's argmin depends on it)."""
    tube = trimesh.creation.annulus(r_min=9, r_max=12, height=400, sections=96)
    monkeypatch.setattr(geometry, "WALL_RAY_BATCH", 10**9)
    whole = geometry._wall_samples(tube, samples, seed)
    monkeypatch.setattr(geometry, "WALL_RAY_BATCH", 997)  # uneven batches
    batched = geometry._wall_samples(tube, samples, seed)
    for one, many in zip(whole, batched):
        assert np.array_equal(one, many)
