"""#1073/#1041: off-track rows never reach a blind-arena trial reader.

Off-track = external (STEP) exports and Blender (mesh) trials; every reader test below runs
once per kind.

One shared helper (``run_log_io.trial_rows``) backs every reader that aggregates,
ranks, reports or queues trials. This plants an external row (and an
external-tagged whole log) in each reader's input and asserts it is excluded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench import advisory_report, code_cad_arena_report as report
from makerbench import code_cad_arena_runner as runner
from makerbench import code_cad_consensus, code_cad_export
from makerbench.arena_studio import analytics
from makerbench.arena_studio.service import ArenaStudioService
from makerbench.run_log_io import is_external_step, trial_rows

EXT = "ext-entrant"
BLIND = "blind-entrant"


def _trial(model: str, tmp: Path, **extra) -> dict:
    png = tmp / f"{model}.png"
    png.write_bytes(b"png")
    stl = tmp / f"{model}.stl"
    stl.write_bytes(b"solid")
    sub = {"compiles": 1.0}
    return {
        "trial_id": f"t-{model}",
        "model_id": model,
        "instrument_id": "inst",
        "seed": 0,
        "rep": 0,
        "status": "scored",
        "result": {
            "render_ok": True,
            "artifacts": {"png_path": str(png), "stl_path": str(stl)},
            "objective": {"objective_pass_rate": 0.5 if model == EXT else 1.0, "sub_scores": sub},
        },
        **extra,
    }


OFF_TRACK_TAGS = {
    "external": {"entrant_type": "external_step", "modality": "external_step", "track": "external_step"},
    "blender": {"result_backend": "blender"},
}


@pytest.fixture(params=sorted(OFF_TRACK_TAGS))
def run_dir(request, tmp_path: Path) -> Path:
    tags = dict(OFF_TRACK_TAGS[request.param])
    off_track = _trial(EXT, tmp_path, **{k: v for k, v in tags.items() if k != "result_backend"})
    if "result_backend" in tags:
        off_track["result"]["backend"] = tags["result_backend"]
    log = {
        "config": {"model_ids": [BLIND], "backend": "openscad"},
        "trials": [_trial(BLIND, tmp_path), off_track],
    }
    (tmp_path / "run_log.json").write_text(json.dumps(log))
    return tmp_path


def _log(run_dir: Path) -> dict:
    return json.loads((run_dir / "run_log.json").read_text())


@pytest.mark.parametrize("where", ["row", "result", "meta"])
@pytest.mark.parametrize("key", ["entrant_type", "modality", "track"])
def test_is_external_step_checks_row_result_and_meta(where: str, key: str) -> None:
    tag = {key: "external_step"}
    entry = tag if where == "row" else {where: tag}
    assert is_external_step(entry)
    assert not is_external_step({"result": {"track": "blind"}})


def test_trial_rows_drops_external_rows_and_whole_external_logs(run_dir: Path) -> None:
    log = _log(run_dir)
    assert [r["model_id"] for r in trial_rows(log)] == [BLIND]
    assert trial_rows({**log, "config": {"track": "external_step"}}) == []
    assert [r["model_id"] for r in trial_rows({"trials": None})] == []


def test_scoreline_excludes_external_row(run_dir: Path) -> None:
    rows = runner.collect_objective_scoreline(_log(run_dir))
    assert {r["entrant"] if "entrant" in r else r["model_id"] for r in rows} == {BLIND}
    assert EXT not in json.dumps(rows)


def test_vote_candidates_exclude_external_row(run_dir: Path) -> None:
    cells = runner.build_vote_candidates(_log(run_dir))
    assert {c.model_id for group in cells.values() for c in group} == {BLIND}


def test_export_scored_trials_exclude_external_row(run_dir: Path) -> None:
    assert [t["model_id"] for t in code_cad_export._scored_trials(_log(run_dir))] == [BLIND]


def test_advisory_report_excludes_external_row(run_dir: Path) -> None:
    assert EXT not in json.dumps(advisory_report.collect_advisory_report(_log(run_dir)))


def test_report_gate_means_and_gallery_exclude_external_row(run_dir: Path) -> None:
    log = _log(run_dir)
    assert set(report.gate_means(log)) == {BLIND}
    assert EXT not in json.dumps(report.gallery_cells(log, run_dir))


def test_report_stat_tiles_count_excludes_external_row(run_dir: Path) -> None:
    log = _log(run_dir)
    payloads = {"run_log": log, "elo": {}, "agreement": {}}
    clean = {"run_log": {**log, "trials": trial_rows(log)}, "elo": {}, "agreement": {}}
    assert report._stat_tiles(payloads) == report._stat_tiles(clean)


def test_consensus_report_excludes_external_row(run_dir: Path) -> None:
    out = code_cad_consensus.build_consensus_report(run_dir, samples=4)
    assert f"t-{EXT}" not in json.dumps(out) and EXT not in json.dumps(out)


def test_family_breakdown_excludes_external_row(run_dir: Path) -> None:
    vote = {
        "pair_id": "p1",
        "voter_id": "v",
        "winner": "left",
        "instrument_id": "inst",
        "seed": 0,
        "reveal": {"left": {"model_id": BLIND}, "right": {"model_id": "other"}},
    }
    (run_dir / "votes.revealed.jsonl").write_text(json.dumps(vote) + "\n")
    out = analytics.per_family_breakdown(run_dir, _log(run_dir), [{"id": "inst", "family": "f"}])
    assert out["f"]["n_trials"] == 1


def test_studio_run_summary_excludes_external_row(run_dir: Path, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": [{"id": "inst", "family": "f"}]}))
    service = ArenaStudioService(registry_path=registry, repo_root=tmp_path)
    summary = service.get_run_summary(run_dir)
    assert [t["model_id"] for t in summary["trials"]] == [BLIND]
    assert summary["compiled_count"] <= 1


def test_whole_blender_log_is_off_track(run_dir: Path) -> None:
    log = _log(run_dir)
    assert trial_rows({**log, "config": {**log["config"], "backend": "blender"}}) == []
    assert trial_rows({**log, "config": {"track": "blender-freeform"}}) == []


def test_orchestrator_summary_counts_only_code_cad_trials(run_dir: Path) -> None:
    """Review finding 3: preserved off-track rows must not inflate the stored summary."""
    from makerbench.code_cad_orchestrator import _merge_and_write

    log = _log(run_dir)

    class Config:  # _merge_and_write only needs the recorded config block
        def as_dict(self):
            return log["config"]

    payload = _merge_and_write(run_dir / "run_log.json", Config(), {"t-" + BLIND: log["trials"][0]})
    assert len(payload["trials"]) == 2  # the writer preserves every row...
    assert payload["summary"]["total_trials"] == 1  # ...the reporting aggregate does not
    assert payload["summary"]["counts"] == {"scored": 1}
