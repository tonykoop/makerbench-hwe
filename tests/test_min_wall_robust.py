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


def test_public_pages_label_robust_policy_rows_separately(tmp_path):
    """#983: native scoreline -> the real publishers. A robust row is published under its own
    estimator label, never in the legacy table every committed round was scored with."""
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
    (published,) = page["rounds"]
    assert [r["entrant"] for r in published["scoreline"]] == ["claude-code-sonnet"]  # legacy table unmixed
    (table,) = published["estimator_scorelines"]
    assert table["min_wall_method"] == "robust-v1" and "robust-v1" in table["label"]
    assert [r["entrant"] for r in table["rows"]] == ["stub-a"]
    assert table["rows"][0]["objective_pass_rate"] == robust_rows[0]["objective_pass_rate"]
    assert "legacy" in published["legacy_scoreline_label"]

    # only-robust round: published, with an empty legacy table and the labelled robust table
    (round_dir / "objective_scoreline.json").write_text(
        json.dumps({"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": robust_rows}), encoding="utf-8")
    (published,) = build_data.build_arena_page(tmp_path / "runs")["rounds"]
    assert published["scoreline"] == [] and len(published["estimator_scorelines"]) == 1

    # an unknown estimator still cannot be labelled: withheld (fail closed)
    unknown = [{**row, "min_wall_method": "future-v9"} for row in robust_rows]
    (round_dir / "objective_scoreline.json").write_text(
        json.dumps({"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": unknown}), encoding="utf-8")
    assert build_data.build_arena_page(tmp_path / "runs") is None

    # legacy-only rounds keep their exact published entry (no new keys)
    (round_dir / "objective_scoreline.json").write_text(
        json.dumps({"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": default_rows}), encoding="utf-8")
    (published,) = build_data.build_arena_page(tmp_path / "runs")["rounds"]
    assert "estimator_scorelines" not in published and "legacy_scoreline_label" not in published

    entry = build_data._arena_run_entry("r", {"scoreline": robust_rows + default_rows,
                                              "run_log": {"config": {"model_ids": ["claude-code-sonnet", "stub-a"]}}})
    assert [r["entrant"] for r in entry["objective_pass_rate"]] == ["claude-code-sonnet"]
    (by_estimator,) = entry["objective_pass_rate_by_estimator"]
    assert by_estimator["min_wall_method"] == "robust-v1"
    assert [r["entrant"] for r in by_estimator["rows"]] == ["stub-a"]
    assert entry["objective_complete"] is True  # every entrant has a (labelled) objective row
    legacy_only = build_data._arena_run_entry("r", {"scoreline": default_rows,
                                                    "run_log": {"config": {"model_ids": ["claude-code-sonnet"]}}})
    assert "objective_pass_rate_by_estimator" not in legacy_only


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


def test_default_gate_casts_wall_rays_in_bounded_batches(tmp_path, monkeypatch, plate_with_a_tiny_blade):
    """#997 memory guard: the production default (robust-v1, 20,000 rays) must reach the
    underlying trimesh ray cast in batches of at most 1,000 rays with multiple_hits=False.
    One unbatched 20,000-ray cast peaked at ~18 GB on a large mesh and OOM-killed CI; this
    fails if _first_hits ever goes back to a single cast, without a flaky RSS threshold."""
    intersector = type(plate_with_a_tiny_blade.ray)
    real = intersector.intersects_location
    calls: list[tuple[int, object]] = []

    def spy(self, ray_origins, ray_directions, *args, **kwargs):
        calls.append((len(ray_origins), kwargs.get("multiple_hits", args[0] if args else None)))
        return real(self, ray_origins, ray_directions, *args, **kwargs)

    monkeypatch.setattr(intersector, "intersects_location", spy)
    result = _payload(tmp_path, plate_with_a_tiny_blade)  # no estimator named: the default
    assert result["min_wall_method"] == "robust-v1"
    assert sum(n for n, _ in calls) >= geometry.ROBUST_V1_SAMPLES
    assert len(calls) >= geometry.ROBUST_V1_SAMPLES // 1000
    assert all(n <= 1000 for n, _ in calls), sorted({n for n, _ in calls})[-3:]
    assert all(multiple_hits is False for _, multiple_hits in calls)


# --- #983 review: agreement and headline are attributed to one estimator, or withheld ---------

def _round(runs, number, rows, rho, n=3):
    round_dir = runs / "code_cad_arena" / f"round{number}"
    round_dir.mkdir(parents=True)
    (round_dir / "objective_scoreline.json").write_text(
        json.dumps({"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": rows}), encoding="utf-8")
    (round_dir / "agreement.json").write_text(
        json.dumps({"agreement": {"rho": rho, "n": n, "interpretation": "x"}}), encoding="utf-8")
    (round_dir / "run_log.json").write_text(
        json.dumps({"config": {"model_ids": sorted({r["entrant"] for r in rows})}}), encoding="utf-8")


def _rows(method, *entrants):
    return [{"entrant": e, "objective_pass_rate": 0.5, "n_objective_trials": 2,
             **({"min_wall_method": method} if method else {})} for e in entrants]


def test_agreement_of_a_mixed_estimator_round_is_withheld(tmp_path):
    build_data = _build_data()
    _round(tmp_path, 5, _rows(None, "a") + _rows("robust-v1", "b", "c"), rho=1.0)
    (published,) = build_data.build_arena_page(tmp_path)["rounds"]
    assert published["agreement"]["rho"] is None and published["agreement"]["n"] is None
    assert "more than one min_wall estimator" in published["agreement"]["withheld_reason"]


def test_agreement_of_robust_plus_unknown_rows_is_withheld(tmp_path):
    """Sol's repro: 2 robust-v1 rows publish, the future-v9 row is withheld, so an n=3 agreement
    computed over all three cannot be shown."""
    build_data = _build_data()
    _round(tmp_path, 5, _rows("robust-v1", "a", "b") + _rows("future-v9", "c"), rho=1.0)
    (published,) = build_data.build_arena_page(tmp_path)["rounds"]
    assert [r["entrant"] for r in published["estimator_scorelines"][0]["rows"]] == ["a", "b"]
    assert published["agreement"]["n"] is None and published["agreement"]["rho"] is None
    assert published["agreement"]["withheld_reason"]
    assert build_data.build_arena_page(tmp_path)["headline"] is None
    assert "estimator_headlines" not in build_data.build_arena_page(tmp_path)

    entry = build_data._arena_run_entry("r", {
        "scoreline": _rows("robust-v1", "a", "b") + _rows("future-v9", "c"),
        "agreement": {"agreement": {"rho": 1.0, "n": 3}},
        "run_log": {"config": {"model_ids": ["a", "b", "c"]}}})
    assert entry["agreement"]["n"] is None and entry["agreement"]["withheld_reason"]


def test_single_estimator_agreement_is_labelled_and_legacy_keeps_its_shape(tmp_path):
    build_data = _build_data()
    _round(tmp_path, 5, _rows(None, "a", "b", "c"), rho=0.5)
    _round(tmp_path, 6, _rows("robust-v1", "a", "b", "c"), rho=-0.5)
    legacy, robust = build_data.build_arena_page(tmp_path)["rounds"]
    assert legacy["agreement"] == {"rho": 0.5, "n": 3, "interpretation": "x"}
    assert robust["agreement"]["rho"] == -0.5 and robust["agreement"]["n"] == 3
    assert robust["agreement"]["min_wall_method"] == "robust-v1" and "robust-v1" in robust["agreement"]["label"]

    entry = build_data._arena_run_entry("r", {
        "scoreline": _rows(None, "a") + _rows("robust-v1", "b"),
        "agreement": {"agreement": {"rho": 1.0, "n": 2}},
        "run_log": {"config": {"model_ids": ["a", "b"]}}})
    assert entry["agreement"]["rho"] is None and entry["agreement"]["withheld_reason"]


def test_headline_never_averages_across_estimators(tmp_path):
    """Legacy rho=+1 and robust rho=-1 must not average into an unlabelled 0; a mixed round
    counts in neither headline."""
    build_data = _build_data()
    _round(tmp_path, 5, _rows(None, "a", "b", "c"), rho=1.0)
    _round(tmp_path, 6, _rows("robust-v1", "a", "b", "c"), rho=-1.0)
    _round(tmp_path, 7, _rows(None, "a") + _rows("robust-v1", "b", "c"), rho=0.25)
    page = build_data.build_arena_page(tmp_path)
    assert page["headline"]["value"] == 1.0 and page["headline"]["rounds_used"] == [5]
    assert page["headline"]["min_wall_method"] == "min" and "legacy" in page["headline"]["label"]
    (robust,) = page["estimator_headlines"]
    assert robust["value"] == -1.0 and robust["rounds_used"] == [6]
    assert robust["min_wall_method"] == "robust-v1" and "robust-v1" in robust["label"]


def test_legacy_only_headline_is_unchanged(tmp_path):
    build_data = _build_data()
    _round(tmp_path, 5, _rows(None, "a", "b", "c"), rho=1.0)
    _round(tmp_path, 6, _rows(None, "a", "b", "c"), rho=0.0)
    page = build_data.build_arena_page(tmp_path)
    assert page["headline"]["value"] == 0.5 and page["headline"]["rounds_used"] == [5, 6]
    assert "label" not in page["headline"] and "estimator_headlines" not in page
# ----- #1007: robust-v1 must not depend on vertex / face order --------------------------

def _holed_tube() -> trimesh.Trimesh:
    """The topology-gate flute: a tube with tone holes cut by (threaded) manifold booleans."""
    tube = trimesh.creation.annulus(r_min=8, r_max=10, height=100, sections=96)
    for z in (30.0, 50.0, 70.0):
        hole = trimesh.creation.cylinder(radius=2.5, height=6, sections=48)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        hole.apply_translation([10.0, 0.0, z])
        tube = tube.difference(hole)
    return tube


def _reordered(mesh: trimesh.Trimesh, seed: int) -> trimesh.Trimesh:
    """Same surface, shuffled vertex order, face order and per-face corner rotation
    (winding kept)."""
    rng = np.random.default_rng(seed)
    vperm = rng.permutation(len(mesh.vertices))
    inverse = np.empty_like(vperm)
    inverse[vperm] = np.arange(len(vperm))
    faces = inverse[mesh.faces][rng.permutation(len(mesh.faces))]
    shift = rng.integers(3, size=len(faces))
    faces = np.take_along_axis(faces, (shift[:, None] + np.arange(3)) % 3, axis=1)
    return trimesh.Trimesh(vertices=mesh.vertices[vperm], faces=faces, process=False)


def test_canonical_mesh_is_order_independent_and_keeps_the_surface():
    mesh = _holed_tube()
    canon = geometry.canonical_mesh(mesh)
    for seed in range(6):
        other = geometry.canonical_mesh(_reordered(mesh, seed))
        assert np.array_equal(other.vertices, canon.vertices)
        assert np.array_equal(other.faces, canon.faces)
    assert canon.is_watertight
    assert canon.volume == pytest.approx(mesh.volume, rel=1e-12)
    # winding (so every face normal) is unchanged: same set of (centroid, normal) pairs
    key = lambda m: np.round(np.hstack([m.triangles_center, m.face_normals]), 9)  # noqa: E731
    assert np.array_equal(np.unique(key(canon), axis=0), np.unique(key(mesh), axis=0))


def test_robust_v1_is_identical_under_vertex_and_face_reordering():
    mesh = _holed_tube()
    want = geometry.estimate_wall_robust_v1(mesh)
    for seed in range(5):
        assert geometry.estimate_wall_robust_v1(_reordered(mesh, seed)) == want


def test_robust_v1_is_identical_in_fresh_processes():
    import subprocess
    import sys

    code = (
        "import json, sys; sys.path.insert(0, 'tests'); import numpy as np\n"
        "from test_min_wall_robust import _holed_tube, _reordered\n"
        "from makerbench import geometry\n"
        "mesh = _holed_tube() if sys.argv[1] == 'raw' else _reordered(_holed_tube(), int(sys.argv[1]))\n"
        "print(json.dumps(geometry.estimate_wall_robust_v1(mesh)))\n")
    outs = {subprocess.run([sys.executable, "-c", code, arg], check=True, capture_output=True, text=True,
                           cwd=Path(__file__).resolve().parents[1]).stdout.strip()
            for arg in ("raw", "11", "29")}
    assert len(outs) == 1, outs
    got = json.loads(outs.pop())
    assert got == geometry.estimate_wall_robust_v1(_holed_tube())


def test_robust_v1_casts_in_bounded_batches(monkeypatch):
    """Canonicalizing must not undo #997's memory guard: no single ray cast exceeds
    WALL_RAY_BATCH rays."""
    sizes = []
    original = trimesh.ray.ray_triangle.RayMeshIntersector.intersects_location

    def spy(self, ray_origins, ray_directions, **kwargs):
        sizes.append(len(ray_origins))
        return original(self, ray_origins, ray_directions, **kwargs)

    monkeypatch.setattr(trimesh.ray.ray_triangle.RayMeshIntersector, "intersects_location", spy)
    monkeypatch.setattr(geometry, "WALL_RAY_BATCH", 1000)
    geometry.estimate_wall_robust_v1(trimesh.creation.annulus(r_min=9, r_max=12, height=40, sections=64))
    assert sizes and max(sizes) <= 1000 and sum(sizes) == geometry.ROBUST_V1_SAMPLES


def _two_boxes(first_thin: bool) -> trimesh.Trimesh:
    """Two disjoint watertight 12-face boxes (a 0.4 mm plate and a 5 mm cube), listed in
    either order: a face-count tie between bodies."""
    thin = trimesh.creation.box(extents=[20.0, 20.0, 0.4])
    cube = trimesh.creation.box(extents=[5.0, 5.0, 5.0])
    cube.apply_translation([40.0, 0.0, 0.0])
    pair = [thin, cube] if first_thin else [cube, thin]
    return trimesh.util.concatenate(pair)


def test_robust_gate_breaks_body_ties_independently_of_order(tmp_path):
    """#1007 review: with two equally large (12-face) solids, robust-v1 measured the
    first-listed one (0.399 mm FAIL vs 4.999 mm PASS for the same geometry). The tie-break
    (face count, volume, canonical geometry) makes the gate order-independent."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _payload(tmp_path / "a", _two_boxes(first_thin=True))
    b = _payload(tmp_path / "b", _two_boxes(first_thin=False))
    assert a["min_wall_method"] == b["min_wall_method"] == "robust-v1"
    assert a["metrics"]["min_wall_mm"] == b["metrics"]["min_wall_mm"]
    assert a["objective_pass_rate"] == b["objective_pass_rate"]
    # the larger-volume body (the 20 x 20 x 0.4 plate) is the one measured
    assert a["metrics"]["min_wall_mm"] < 1.0


def test_legacy_min_gate_keeps_first_listed_body_selection(tmp_path):
    """Legacy "min" is unchanged (older results must reproduce): it still measures the
    first-listed of equally large bodies."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _payload(tmp_path / "a", _two_boxes(first_thin=True), min_wall_estimator="min")
    b = _payload(tmp_path / "b", _two_boxes(first_thin=False), min_wall_estimator="min")
    assert a["min_wall_method"] == b["min_wall_method"] == "min"
    assert a["metrics"]["min_wall_mm"] < 1.0 < b["metrics"]["min_wall_mm"]


def test_canonical_rotation_is_unique_for_degenerate_faces():
    faces = np.array([[2, 0, 0], [0, 2, 0], [0, 0, 2], [1, 3, 1], [3, 1, 1], [1, 1, 3]])
    rotated = geometry._smallest_rotation(faces)
    assert rotated.tolist() == [[0, 0, 2]] * 3 + [[1, 1, 3]] * 3
    verts = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    one = geometry.canonical_mesh(trimesh.Trimesh(verts, faces[[0, 3]], process=False))
    two = geometry.canonical_mesh(trimesh.Trimesh(verts, faces[[4, 2]], process=False))
    assert np.array_equal(one.faces, two.faces) and np.array_equal(one.vertices, two.vertices)


def test_welding_coincident_vertices_is_sampling_only():
    """Documented limitation: two closed boxes touching along an edge are watertight as
    listed, but exact-coordinate welding joins the shared edge, so the canonical copy is
    not. Watertightness is judged on the original mesh, and the robust-v1 reading stays
    finite and order-independent."""
    a = trimesh.creation.box(extents=[2.0, 2.0, 2.0])
    b = trimesh.creation.box(extents=[2.0, 2.0, 2.0])
    b.apply_translation([2.0, 2.0, 0.0])  # shares the x=1, y=1 edge with a
    mesh = trimesh.Trimesh(np.vstack([a.vertices, b.vertices]),
                           np.vstack([a.faces, b.faces + len(a.vertices)]), process=False)
    assert mesh.is_watertight
    assert not geometry.canonical_mesh(mesh).is_watertight
    want = geometry.estimate_wall_robust_v1(mesh)
    assert np.isfinite(want["wall_mm"]) and want["wall_mm"] > 1.9
    for seed in range(3):
        assert geometry.estimate_wall_robust_v1(_reordered(mesh, seed)) == want
