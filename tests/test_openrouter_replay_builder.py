"""Builder and site-publication contract for the OpenRouter replay bundle (#OpenRouter run). Fixture only: no run dirs, no network."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("makerbench_build_openrouter_replay", ROOT / "scripts" / "build_openrouter_replay.py")
replay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = replay
SPEC.loader.exec_module(replay)

BUILD = importlib.util.spec_from_file_location("makerbench_site_build_data_or", ROOT / "site" / "build_data.py")
site = importlib.util.module_from_spec(BUILD)
BUILD.loader.exec_module(site)


def _trial(instrument, seed, status, *, rate=None, sub=None, error=None, stage=None):
    t = {"trial_id": f"{instrument}__seed{seed}__rep0", "instrument_id": instrument, "seed": seed, "rep": 0,
         "status": status, "attempts": 1, "wall_time_s": 1.5, "error": error, "result": None}
    if status == "scored":
        t["result"] = {"objective": {"objective_pass_rate": rate, "sub_scores": sub}}
    elif status == "auto_fail":
        t["result"] = {"failure_stage": stage, "error": "x"}
    return t


def _fixture(tmp_path):
    """All six models x all ten rounds, every cell scored perfectly except a few crafted ones."""
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    lines = []
    for rnd, (instruments, seeds) in replay.ROUNDS.items():
        for entrant in replay.MODELS:
            trials = []
            for instrument in instruments:
                for seed in seeds:
                    trials.append(_trial(instrument, seed, "scored", rate=1.0,
                                         sub={k: 1.0 for k in ("renders", "watertight", "min_wall")}))
                    lines.append({"kind": "settle", "id": f"{entrant}{rnd}{instrument}{seed}", "entrant": entrant,
                                  "instrument": instrument, "seed": seed, "model": "m", "cost_usd": 0.001})
            run = runs / f"r{rnd}-{entrant.split('openrouter-', 1)[1]}"
            run.mkdir(parents=True)
            if rnd == 1 and entrant == "openrouter-deepseek-v4-pro":
                trials[0] = _trial(trials[0]["instrument_id"], 0, "error", error="generation error: returned no fenced code block")
                trials[1] = _trial(trials[1]["instrument_id"], 1, "auto_fail", stage="openscad_render")
                trials[2] = _trial(trials[2]["instrument_id"], 0, "error", error="Command ['openscad'] timed out after 120 seconds")
                trials[3] = _trial(trials[3]["instrument_id"], 1, "scored", rate=0.833333, sub={"renders": 1.0, "watertight": 1.0, "min_wall": 0.0})
            (run / "run_log.json").write_text(json.dumps({"trials": trials}), encoding="utf-8")
    ledger.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return runs, ledger


def _regrade(runs):
    out = {}
    for log in runs.glob("r*/run_log.json"):
        for t in json.loads(log.read_text(encoding="utf-8"))["trials"]:
            if t["status"] == "scored":
                # the baseline gate is harsher on one cell: min_wall fails
                bad = log.parent.name == "r1-deepseek-v4-flash" and t["instrument_id"] == "ocarina"
                sub = {"renders": 1.0, "watertight": 1.0, "min_wall": 0.0 if bad else (t["result"]["objective"]["sub_scores"].get("min_wall"))}
                out[f"{log.parent.name}::{t['trial_id']}"] = {"status": "scored", "failure_stage": None,
                                                              "objective_pass_rate": 0.833333 if bad else t["result"]["objective"]["objective_pass_rate"],
                                                              "sub_scores": sub}
    return out


def _bundle(tmp_path):
    runs, ledger = _fixture(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for rel in replay.HARNESS_FILES:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"], cwd=repo, check=True)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    return replay.build(runs, ledger, rev, repo, (1.0, 2.0), _regrade(runs), rev), runs


def test_primary_rows_are_baseline_graded_and_current_gate_is_labelled_secondary(tmp_path):
    bundle, _ = _bundle(tmp_path)
    flash = next(m for m in bundle["matchups"] if m["id"] == "or-r1-ocarina-seed0-rep0")
    row = next(e for e in flash["entrants"] if e["entrant"] == "openrouter-deepseek-v4-flash")
    assert row["objective_pass_rate"] == 0.833333 and row["failed_checks"] == {"min_wall": 1}
    assert row["current_gate"] == {"objective_pass_rate": 1.0, "failed_checks": {}}
    assert bundle["openrouter_run"]["grading"]["primary_rows"].startswith("gate replay")


def test_error_classification_keeps_infrastructure_compile_and_auto_fail_apart(tmp_path):
    bundle, _ = _bundle(tmp_path)
    cov = bundle["coverage"]
    assert (cov["infrastructure_errors"], cov["compile_errors"], cov["render_auto_fail"]) == (1, 1, 1)
    pro = next(m for m in bundle["matchups"] if m["id"] == "or-r1-ocarina-seed0-rep0")
    row = next(e for e in pro["entrants"] if e["entrant"] == "openrouter-deepseek-v4-pro")
    assert row["n_infra_errors"] == 1 and row["objective_pass_rate"] is None and row["n_objective_trials"] == 0
    assert cov["planned"] == cov["completed"] == 396 and cov["pending"] == 0


def test_bundle_is_accepted_by_the_site_publisher_and_leaks_no_private_fields(tmp_path):
    bundle, _ = _bundle(tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    (results / "b.json").write_text(json.dumps(bundle), encoding="utf-8")
    page = site.build_matchups_page(results)
    assert len(page["matchups"]) == 66
    published = json.dumps(page).lower()
    for forbidden in ("elo", "vote", "current_gate", "cost_usd", "source_sha256"):
        assert forbidden not in published


def test_an_unfinished_trial_is_refused(tmp_path):
    runs, ledger = _fixture(tmp_path)
    log = runs / "r2-grok-4.3" / "run_log.json"
    data = json.loads(log.read_text(encoding="utf-8"))
    data["trials"][0]["status"] = "pending"
    log.write_text(json.dumps(data), encoding="utf-8")
    try:
        replay.build(runs, ledger, "HEAD", tmp_path)
    except RuntimeError as exc:
        assert "unfinished trial" in str(exc)
    else:
        raise AssertionError("a pending trial must not be published")
