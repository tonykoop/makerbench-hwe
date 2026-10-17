"""Readers of a recorded objective rescore it without the retired wall check (#1011).

Each case stores an old record whose rate counted a failed (or passed) wall check and shows the
reader publishing the rate a current run would give: the winner export, the consensus report,
saved workbench revisions and the post-vote judge panel. The recorded files are never changed.
"""

from __future__ import annotations

import json

import pytest

from makerbench import arena_checks as checks
from makerbench.code_cad_export import pick_winners

FIVE = dict.fromkeys(checks.ARENA_GATE_CHECKS, 1.0)


def _trial(model: str, *, failed: str | None, wall: float, seed: int = 0) -> dict:
    sub = {**FIVE, "min_wall": wall}
    if failed:
        sub[failed] = 0.0
    stored = sum(sub.values()) / len(sub)           # the old six-check rate
    return {"trial_id": f"t-{model}-{seed}", "model_id": model, "instrument_id": "ocarina", "seed": seed, "rep": 0,
            "status": "scored", "result": {"objective": {"objective_pass_rate": stored, "sub_scores": sub}}}


def test_accessor_ignores_the_retired_check_everywhere():
    row = _trial("a", failed=None, wall=0.0)
    assert row["result"]["objective"]["objective_pass_rate"] == pytest.approx(5 / 6)
    assert checks.trial_objective_rate(row) == 1.0
    assert "min_wall" not in checks.retained_checks(row["result"]["objective"])
    shown = checks.current_objective(row["result"]["objective"])
    assert shown["objective_pass_rate"] == 1.0 and "min_wall" not in shown["sub_scores"]
    assert row["result"]["objective"]["sub_scores"]["min_wall"] == 0.0   # the record itself is untouched


def test_winner_export_ranks_by_the_retained_checks(tmp_path):
    # a passes all five retained checks but failed the old wall check; z fails watertight but passed it.
    # Both stored six-check rates are 5/6, so ranking by the stored rate picks z; the current scores are a=1.0, z=0.8.
    run_log = {"trials": [_trial("a", failed=None, wall=0.0), _trial("z", failed="watertight", wall=1.0)]}
    votes = tmp_path / "votes.revealed.jsonl"
    votes.write_text("", encoding="utf-8")
    winner = pick_winners(run_log, votes)["ocarina"]
    assert winner["entrant"] == "a"


def test_winner_export_best_trial_uses_the_retained_checks(tmp_path):
    run_log = {"trials": [_trial("a", failed="watertight", wall=1.0, seed=0), _trial("a", failed=None, wall=0.0, seed=1)]}
    votes = tmp_path / "votes.revealed.jsonl"
    votes.write_text("", encoding="utf-8")
    winner = pick_winners(run_log, votes)["ocarina"]
    assert winner["trial"]["trial_id"] == "t-a-1"


def test_saved_revision_shows_five_checks_and_the_rescored_rate(tmp_path):
    from makerbench.workbench_store import WorkbenchStore

    store = WorkbenchStore(tmp_path / "runs" / "workbench")
    design = store.create_design(instrument_id="ukulele", backend="openscad", title="Uke",
                                 origin={"kind": "master", "instrument_id": "ukulele",
                                         "repo_rel_path": "strings/ukulele", "file": "ukulele.scad"})
    did = design["design_id"]
    human = {"kind": "human", "voter": "tony"}
    draft = store.create_draft(did, parent_rev_id=None, source="cube(1);\n", editor=human)
    ddir = store.draft_dir(did, draft["draft_id"])
    recorded = {"render_ok": True, "objective": {"declared": True, "objective_pass_rate": 5 / 6,
                                                 "sub_scores": {**FIVE, "min_wall": 0.0}}}
    (ddir / "objective.json").write_text(json.dumps(recorded), encoding="utf-8")
    store.update_draft_job(did, draft["draft_id"], status="succeeded", exit_code=0)
    rev = store.save_revision(did, draft_id=draft["draft_id"])
    for shown in (store.read_revision(did, rev["rev_id"])["objective"],):
        assert shown["objective"]["objective_pass_rate"] == 1.0
        assert "min_wall" not in shown["objective"]["sub_scores"]
    on_disk = json.loads((store.revision_dir(did, rev["rev_id"]) / "objective.json").read_text())
    assert on_disk == recorded                                            # recorded file untouched


def test_consensus_report_publishes_rescored_rates(tmp_path):
    from tests.test_code_cad_consensus import _central_and_outliers, _trial
    from makerbench import code_cad_consensus as consensus

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trials = []
    for i, mesh in enumerate(list(_central_and_outliers().values())[:3]):
        trial = _trial(run_dir, f"plate__seed{i}__rep0__model-a", "model-a", mesh, rate=5 / 6, seed=i)
        trial["result"]["objective"]["sub_scores"] = {**FIVE, "min_wall": 0.0}
        trials.append(trial)
    (run_dir / "run_log.json").write_text(json.dumps({"config": {}, "trials": trials, "summary": {}}), encoding="utf-8")
    (selection,) = consensus.build_consensus_report(run_dir, samples=256)["selections"]
    assert selection["selected_objective_pass_rate"] == 1.0
    assert selection["single_shot_mean_objective_pass_rate"] == 1.0


def test_post_vote_judge_panel_shows_the_rescored_objective(tmp_path):
    from makerbench.arena_studio.service import ArenaStudioService

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trial = _trial("a", failed=None, wall=0.0)
    (run_dir / "run_log.json").write_text(json.dumps({"trials": [trial]}), encoding="utf-8")
    (run_dir / "votes.blind.jsonl").write_text(json.dumps({"pair_id": "p1", "voter_id": "tony"}) + "\n", encoding="utf-8")
    pair = {"pair_id": "p1", "voter_id": "tony", "winner": "left",
            "reveal": {"left": {"trial_id": trial["trial_id"], "model_id": "a"}, "right": {}}}
    (run_dir / "votes.revealed.jsonl").write_text(json.dumps(pair) + "\n", encoding="utf-8")
    service = ArenaStudioService.__new__(ArenaStudioService)
    panel = service.get_judge_panel(run_dir, "p1", "tony")
    assert panel["left"]["objective"]["objective_pass_rate"] == 1.0
    assert "min_wall" not in panel["left"]["objective"]["sub_scores"]
