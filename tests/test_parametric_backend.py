"""Tests for the deterministic parametric arena backend."""
from __future__ import annotations

from pathlib import Path

from makerbench import parametric_backend as pb
from makerbench.code_cad_orchestrator import ArenaTrial

_REGISTRY = {
    "instruments": [
        {
            "id": "trumpet-sheetmetal",
            "family": "brass",
            "task_brief": "A Bb trumpet with a flared bell.",
            "envelope_mm": [560, 200, 180],
            "min_bodies": 1,
            "min_wall_mm": 0.5,
        },
        {"id": "no-generator-instrument", "family": "unknownfam", "envelope_mm": [100, 100, 100]},
    ]
}


def test_unavailable_instruments_flags_missing_generator():
    missing = pb.unavailable_instruments(_REGISTRY, ["trumpet-sheetmetal", "no-generator-instrument"])
    assert missing == ["no-generator-instrument"]          # trumpet resolves (id + family)


def test_parametric_trial_produces_scored_brep_row(tmp_path):
    execute = pb.make_parametric_execute_trial(registry=_REGISTRY, run_dir=tmp_path)
    trial = ArenaTrial(instrument_id="trumpet-sheetmetal", model_id="parametric-trumpet",
                       seed=0, rep=0, provider="parametric",
                       trial_id="trumpet-sheetmetal__seed0__rep0__parametric-trumpet")
    row = execute(trial)
    assert row["backend"] == "parametric"
    assert row["tier"] == "parametric"
    assert row["render_ok"] is True
    assert row["status"] in {"scored", "auto_fail"}
    # deterministic generator -> real watertight geometry clears the mesh gate's core checks
    sub = row["objective"]["sub_scores"]
    assert sub["watertight"] == 1.0 and sub["nonzero_volume"] == 1.0
    assert Path(row["gen"]["stl_path"]).exists()
    assert "trumpet" in row["gen"]["generator"]


def test_parametric_is_deterministic(tmp_path):
    execute = pb.make_parametric_execute_trial(registry=_REGISTRY, run_dir=tmp_path)

    def run(tid):
        t = ArenaTrial(instrument_id="trumpet-sheetmetal", model_id="p", seed=0, rep=0,
                       provider="parametric", trial_id=tid)
        r = execute(t)
        return r["objective"]["sub_scores"], Path(r["gen"]["stl_path"]).stat().st_size

    a = run("t__seed0__rep0__a")
    b = run("t__seed0__rep0__b")
    assert a == b                                          # same geometry every run
