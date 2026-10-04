"""#997 review: the min_wall policy follows every trial, scored or failed, on every path.

P1: the live and parametric executors (and ingestion) lost ``min_wall_method`` on failures,
so one robust-v1 success plus one failure split into a robust 1.0/1 row and a legacy 0.0/1
row, and the site published only the zero.

P2: ``make_execute_trial`` overwrote the gate's actual method with the registry/default
policy, so a gate built with ``min_wall_estimator="min"`` was reported as robust-v1.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import live_cad_runner as live
from makerbench import parametric_backend as pb
from makerbench.code_cad_objective import RenderArtifacts
from makerbench.code_cad_orchestrator import ArenaTrial, OrchestrationConfig, run_orchestration
from makerbench.code_cad_providers import make_stub_generator
from makerbench.render import CompileError

BOX_REGISTRY = {
    "instruments": [
        {"id": "boxolin", "task_brief": "a box instrument", "envelope_mm": [100, 100, 100], "min_bodies": 1},
    ]
}

LIVE_REGISTRY = {
    "instruments": [
        {"id": "trumpet-sheetmetal", "family": "brass", "task_brief": "A Bb trumpet with a flared bell.",
         "envelope_mm": [533, 200, 180], "min_bodies": 1, "min_wall_mm": 0.6},
    ]
}

PARAMETRIC_REGISTRY = {
    "instruments": [
        {"id": "trumpet-sheetmetal", "family": "brass", "task_brief": "A Bb trumpet with a flared bell.",
         "envelope_mm": [560, 200, 180], "min_bodies": 1, "min_wall_mm": 0.5},
        {"id": "no-generator-instrument", "family": "unknownfam", "envelope_mm": [100, 100, 100]},
    ]
}


def _min_gate(spec):
    return runner.mesh_objective_gate(spec, min_wall_estimator="min")


def _box_compiler(scad_path: Path, out_dir: Path) -> RenderArtifacts:
    out_dir.mkdir(parents=True, exist_ok=True)
    stl = out_dir / "output.stl"
    trimesh.creation.box(extents=[30, 20, 10]).export(stl)
    png = out_dir / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    return RenderArtifacts(stl_path=stl, png_path=png)


def _broken_compiler(scad_path: Path, out_dir: Path) -> RenderArtifacts:
    raise CompileError("openscad exploded")


def _live_config(tmp_path: Path, *, fail_seeds=()) -> live.LiveCadConfig:
    cfg = live.LiveCadConfig(
        backend="solidworks-live",
        driver_model="gpt-5.6-sol",
        win_staging_root=str(tmp_path / "win"),
        wsl_staging_root=str(tmp_path / "win"),  # same dir stands in for the /mnt/c bridge
    )

    def fake_runner(argv, **kwargs):
        win_stl = [ln for ln in argv[-1].splitlines() if ln.strip().endswith("output.stl")][-1].strip()
        trial_id = Path(win_stl.replace("\\", "/")).parent.name
        if not any(f"__seed{s}__" in trial_id for s in fail_seeds):
            out = Path(cfg.wsl_staging_root) / trial_id / "output.stl"
            out.parent.mkdir(parents=True, exist_ok=True)
            trimesh.creation.box(extents=(120.0, 60.0, 40.0)).export(out)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    cfg.runner = fake_runner
    return cfg


# --- P1: failures stay in their policy's row --------------------------------------------------


def test_live_success_and_failure_share_one_robust_row(tmp_path):
    config = OrchestrationConfig(instrument_ids=("trumpet-sheetmetal",), model_ids=("codex-sw",),
                                 seeds=(0, 1), reps=1)
    execute = live.make_live_execute_trial(registry=LIVE_REGISTRY, run_dir=tmp_path / "run",
                                           config=_live_config(tmp_path, fail_seeds=(1,)))
    log = run_orchestration(config=config, run_log_path=tmp_path / "run_log.json", execute_trial=execute)

    by_seed = {t["seed"]: t for t in log["trials"]}
    assert by_seed[0]["result"]["objective"]["min_wall_method"] == "robust-v1"
    assert by_seed[1]["status"] == "error"
    assert by_seed[1]["meta"]["min_wall_method"] == "robust-v1"
    assert by_seed[1]["meta"]["backend"] == "solidworks-live"

    rows = runner.collect_objective_scoreline(log)
    assert len(rows) == 1, rows  # never a robust 1.0/1 row next to a legacy 0.0/1 row
    assert rows[0]["min_wall_method"] == "robust-v1"
    assert rows[0]["n_objective_trials"] == 2


def test_parametric_failure_keeps_robust_policy(tmp_path):
    config = OrchestrationConfig(instrument_ids=("trumpet-sheetmetal", "no-generator-instrument"),
                                 model_ids=("parametric-x",), seeds=(0,), reps=1)
    execute = pb.make_parametric_execute_trial(registry=PARAMETRIC_REGISTRY, run_dir=tmp_path / "run")
    log = run_orchestration(config=config, run_log_path=tmp_path / "run_log.json", execute_trial=execute)

    failed = next(t for t in log["trials"] if t["instrument_id"] == "no-generator-instrument")
    assert failed["status"] == "error"
    assert failed["meta"] == {"backend": "parametric", "tier": "parametric", "min_wall_method": "robust-v1"}
    scored = next(t for t in log["trials"] if t["instrument_id"] == "trumpet-sheetmetal")
    assert scored["result"]["objective"]["min_wall_method"] == "robust-v1"
    rows = runner.collect_objective_scoreline(log)
    assert [(r.get("min_wall_method"), r["n_objective_trials"]) for r in rows] == [("robust-v1", 2)]


def test_compile_failure_auto_fail_rows_carry_policy_on_every_executor(tmp_path):
    # make_execute_trial: compile failure -> auto_fail payload, stamped with the gate's policy.
    execute = runner.make_execute_trial(backend="openscad", registry=BOX_REGISTRY, run_dir=tmp_path / "a",
                                        generators={"stub": make_stub_generator()},
                                        compiler=_broken_compiler)
    payload = execute(ArenaTrial(instrument_id="boxolin", model_id="stub", seed=0, rep=0, provider="stub",
                                 trial_id="boxolin__seed0__rep0__stub"))
    assert payload["status"] == "auto_fail"
    assert payload["objective"]["min_wall_method"] == "robust-v1"


def test_ingested_compile_failure_keeps_policy(tmp_path):
    log_path = tmp_path / "run_log.json"
    log_path.write_text(json.dumps({"trials": []}), encoding="utf-8")
    scad = tmp_path / "external.scad"
    scad.write_text("cube([5,5,5]);\n", encoding="utf-8")
    entry = runner.ingest_candidate(run_log_path=log_path, registry=BOX_REGISTRY, instrument_id="boxolin",
                                    model_id="cadam", scad_path=scad, run_dir=tmp_path,
                                    compiler=_broken_compiler)
    assert entry["status"] == "auto_fail"
    assert entry["result"]["objective"]["min_wall_method"] == "robust-v1"

    min_entry = runner.ingest_candidate(run_log_path=log_path, registry=BOX_REGISTRY, instrument_id="boxolin",
                                        model_id="cadam-min", scad_path=scad, run_dir=tmp_path,
                                        compiler=_broken_compiler, gate_factory=_min_gate)
    assert min_entry["result"]["objective"]["min_wall_method"] == "min"


# --- P2: the gate's actual method wins over the registry/default policy -----------------------


def test_execute_trial_preserves_gate_method_on_success_and_failure(tmp_path):
    config = OrchestrationConfig(instrument_ids=("boxolin",), model_ids=("ok", "broken"), seeds=(0,), reps=1)

    def broken_generator(request):
        raise RuntimeError("generation failed")

    execute = runner.make_execute_trial(backend="openscad", registry=BOX_REGISTRY, run_dir=tmp_path,
                                        generators={"ok": make_stub_generator(), "broken": broken_generator},
                                        compiler=_box_compiler, gate_factory=_min_gate)
    log = run_orchestration(config=config, run_log_path=tmp_path / "run_log.json", execute_trial=execute)

    by_model = {t["model_id"]: t for t in log["trials"]}
    assert by_model["ok"]["result"]["objective"]["min_wall_method"] == "min"
    assert by_model["ok"]["result"]["objective"].get("metrics", {}).get("min_wall_method", "min") == "min"
    assert by_model["broken"]["meta"]["min_wall_method"] == "min"
    # Both legacy-scored: unmarked rows, nothing labelled robust-v1.
    rows = runner.collect_objective_scoreline(log)
    assert all("min_wall_method" not in r for r in rows)


def test_compile_failure_with_min_gate_is_recorded_as_min(tmp_path):
    execute = runner.make_execute_trial(backend="openscad", registry=BOX_REGISTRY, run_dir=tmp_path,
                                        generators={"stub": make_stub_generator()},
                                        compiler=_broken_compiler, gate_factory=_min_gate)
    payload = execute(ArenaTrial(instrument_id="boxolin", model_id="stub", seed=0, rep=0, provider="stub",
                                 trial_id="boxolin__seed0__rep0__stub"))
    assert payload["status"] == "auto_fail"
    assert payload["objective"]["min_wall_method"] == "min"


def test_live_and_parametric_preserve_min_gate(tmp_path):
    live_exec = live.make_live_execute_trial(registry=LIVE_REGISTRY, run_dir=tmp_path / "live",
                                             config=_live_config(tmp_path, fail_seeds=(1,)), gate_factory=_min_gate)
    ok = live_exec(ArenaTrial(instrument_id="trumpet-sheetmetal", model_id="m", seed=0, rep=0,
                              provider="solidworks-live", trial_id="trumpet-sheetmetal__seed0__rep0__m"))
    assert ok["objective"]["min_wall_method"] == "min"
    with pytest.raises(CompileError) as excinfo:
        live_exec(ArenaTrial(instrument_id="trumpet-sheetmetal", model_id="m", seed=1, rep=0,
                             provider="solidworks-live", trial_id="trumpet-sheetmetal__seed1__rep0__m"))
    assert excinfo.value.trial_meta == {"backend": "solidworks-live", "tier": "live", "context_tier": "blind",
                                        "min_wall_method": "min"}

    par_exec = pb.make_parametric_execute_trial(registry=PARAMETRIC_REGISTRY, run_dir=tmp_path / "par",
                                                gate_factory=_min_gate)
    with pytest.raises(KeyError) as excinfo:
        par_exec(ArenaTrial(instrument_id="no-generator-instrument", model_id="p", seed=0, rep=0,
                            provider="parametric", trial_id="no-generator-instrument__seed0__rep0__p"))
    assert excinfo.value.trial_meta == {"backend": "parametric", "tier": "parametric", "min_wall_method": "min"}


def test_gate_declares_its_policy():
    spec = BOX_REGISTRY["instruments"][0]
    assert runner.mesh_objective_gate(spec).min_wall_method == "robust-v1"
    assert _min_gate(spec).min_wall_method == "min"
    assert runner.gate_min_wall_policy(_min_gate(spec), {**spec, "min_wall_estimator": "robust-v1"}) == "min"
    # A gate that declares nothing falls back to the spec's policy.
    assert runner.gate_min_wall_policy(lambda ctx: {}, spec) == "robust-v1"


def test_stamp_never_overwrites_gate_marker():
    payload = {"objective": {"min_wall_method": "min"}}
    assert runner.stamp_min_wall_policy(payload, "robust-v1")["objective"]["min_wall_method"] == "min"
    empty = {"objective": {}}
    assert runner.stamp_min_wall_policy(empty, "robust-v1")["objective"]["min_wall_method"] == "robust-v1"
    assert runner.stamp_min_wall_policy({"objective": {}}, "")["objective"] == {}
