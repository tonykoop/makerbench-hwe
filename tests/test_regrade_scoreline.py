"""scripts/regrade_scoreline.py: never writes into the recorded run, and keeps unregraded
(no-mesh) failures in their entrant's row under today's min_wall policy."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("regrade_scoreline", ROOT / "scripts/regrade_scoreline.py")
regrade_scoreline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regrade_scoreline)


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    log = {"config": {"backend": "openscad"}, "trials": [
        {"trial_id": "ocarina__seed1__rep0__m", "instrument_id": "ocarina", "model_id": "m", "seed": 1,
         "status": "auto_fail", "result": None, "meta": None}]}
    (run / "run_log.json").write_text(json.dumps(log), encoding="utf-8")
    return run


@pytest.mark.parametrize("inside", [True, False])
def test_refuses_an_output_overlapping_the_recorded_run(tmp_path, inside):
    run = _run(tmp_path)
    out = run / "regraded" if inside else run
    with pytest.raises(SystemExit, match="outside --run-dir"):
        regrade_scoreline.regrade(run, out, ROOT / "tasks/code_cad_arena/registry.json", 1)


def test_unregraded_failure_keeps_the_policy_row_and_the_source_is_untouched(tmp_path):
    run = _run(tmp_path)
    before = (run / "run_log.json").read_bytes()
    scoreline = tmp_path / "scoreline.json"
    regrade_scoreline.main(["--run-dir", str(run), "--out-run", str(tmp_path / "out"),
                            "--scoreline-out", str(scoreline), "--workers", "1"])
    assert (run / "run_log.json").read_bytes() == before
    regraded = json.loads((tmp_path / "out/run_log.json").read_text())
    assert regraded["trials"][0]["meta"]["min_wall_method"] == "robust-v1"
    assert regraded["regrade"]["trials_regraded"] == 0
    rows = json.loads(scoreline.read_text())["rows"]
    assert len(rows) == 1 and rows[0]["min_wall_method"] == "robust-v1"
    assert rows[0]["objective_pass_rate"] == 0.0 and rows[0]["n_objective_trials"] == 1
