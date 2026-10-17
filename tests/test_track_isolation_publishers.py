"""Replay builders, site publishers and the raw-access guard keep off-track rows out of code-CAD numbers.

Off-track = Blender (mesh) trials (#1041) and external STEP exports (#1069); each test runs once per kind.

The reviewer's synthetic probe: one model with OpenSCAD rate 0 and Blender rate 1 on public
dev seeds. The builders must not average them, relabel Blender as OpenSCAD, or publish it in
the ordinary comparison table; and the guard test pins that no other code reads trials raw.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

import test_openrouter_replay_builder as ort

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "frontier_replay"

_spec = importlib.util.spec_from_file_location(
    "build_frontier_replay_iso", ROOT / "scripts" / "build_frontier_replay.py"
)
frontier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frontier)


def _load(name):
    return json.loads((FIXTURES / name).read_text())


KINDS = ("blender", "external")


@pytest.fixture(params=KINDS)
def kind(request):
    return request.param


def _tag(kind: str, trial: dict) -> dict:
    """Mark a trial/row as coming from another track."""
    if kind == "blender":
        trial.setdefault("result", {})
        if isinstance(trial["result"], dict):
            trial["result"]["backend"] = "blender"
        trial["backend"] = "blender"
    else:
        trial.update(entrant_type="external_step", modality="external_step", track="external_step")
    return trial


def _off_track_trial(kind: str, base: dict, **overrides) -> dict:
    trial = copy.deepcopy(base)
    trial.update(trial_id=f"{kind}-" + str(base["trial_id"]), status="scored", error=None)
    trial["result"] = {"objective": {"objective_pass_rate": 1.0, "sub_scores": {"first": 1.0, "second": 1.0}}}
    trial.update(overrides)
    return _tag(kind, trial)


@pytest.fixture
def frontier_runs(tmp_path):
    run = tmp_path / "runs" / "round1"
    run.mkdir(parents=True)
    (run / "run_log.json").write_bytes((FIXTURES / "run_log.json").read_bytes())
    for i in range(3):
        gen = run / "gen" / f"fixture-{i}"
        gen.mkdir(parents=True)
        (gen / "placeholder.scad").write_bytes(b"synthetic source placeholder\n")
    return run.parent


def _build_frontier(runs):
    return frontier.build(runs, _load("prior.json"), _load("recording.json"))


def test_frontier_replay_ignores_off_track_trials(frontier_runs, kind):
    expected = _build_frontier(frontier_runs)
    assert (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
        == (FIXTURES / "expected.json").read_text()
    )
    log_path = frontier_runs / "round1" / "run_log.json"
    log = json.loads(log_path.read_text())
    first = log["trials"][0]
    # a Blender rate-1 trial on a fresh seed, and one colliding with an OpenSCAD cell's key
    log["trials"] += [_off_track_trial(kind, first, seed=9), _off_track_trial(kind, first)]
    log_path.write_text(json.dumps(log))
    mixed = _build_frontier(frontier_runs)
    assert mixed == expected  # no averaging, no relabelled backend, no cell-matrix error
    assert kind not in json.dumps(mixed).lower().replace("external_step", "external")
    # the review probe: tag the REAL scored Gemini cell itself and give it a distinct score
    solo = json.loads(log_path.read_text())
    solo["trials"] = [copy.deepcopy(t) for t in log["trials"][:4]]
    _tag(kind, solo["trials"][0])["result"]["objective"]["objective_pass_rate"] = 0.123456
    log_path.write_text(json.dumps(solo))
    with pytest.raises(ValueError, match="cell matrices"):  # refused, never published
        _build_frontier(frontier_runs)


def test_frontier_replay_refuses_a_whole_off_track_run(frontier_runs, kind):
    log_path = frontier_runs / "round1" / "run_log.json"
    log = json.loads(log_path.read_text())
    log["config"] = {"backend": "blender"} if kind == "blender" else {"track": "external_step"}
    log_path.write_text(json.dumps(log))
    with pytest.raises(ValueError, match="cell matrices"):
        _build_frontier(frontier_runs)


def test_openrouter_replay_ignores_off_track_trials(tmp_path, monkeypatch, kind):
    baseline, _runs = ort._bundle(tmp_path / "a")
    original = ort._fixture

    def mixed_fixture(path):
        runs, ledger = original(path)
        for log_path in runs.glob("r*/run_log.json"):
            log = json.loads(log_path.read_text())
            log["trials"] += [
                _off_track_trial(kind, log["trials"][0], seed=9),  # fresh key
                # same cell key as a blind row: indexed before filtering, it replaced that row
                _off_track_trial(
                    kind,
                    log["trials"][1],
                    result={"objective": {"objective_pass_rate": 0.123456, "sub_scores": {"renders": 1.0}}},
                ),
            ]
            log_path.write_text(json.dumps(log))
        return runs, ledger

    monkeypatch.setattr(ort, "_fixture", mixed_fixture)
    mixed, _ = ort._bundle(tmp_path / "b")

    def normalised(bundle):  # the fixture repo's commit hash differs per tmp dir
        return json.dumps(bundle, sort_keys=True).replace(bundle["harness_git_revision"], "REV")

    # same rates, same backend labels: the off-track trials never entered
    assert normalised(mixed) == normalised(baseline)
    assert "0.123456" not in json.dumps(mixed)
    # builder output -> site Matchups publisher: the planted score never reaches public data
    results = tmp_path / "results"
    results.mkdir()
    (results / "b.json").write_text(json.dumps(mixed))
    assert "0.123456" not in json.dumps(ort.site.build_matchups_page(results))


def test_matchups_publisher_routes_off_track_entrants_out(tmp_path, kind):
    bundle, _ = ort._bundle(tmp_path / "src")
    results = tmp_path / "results"
    results.mkdir()
    (results / "clean.json").write_text(json.dumps(bundle))
    clean_page = ort.site.build_matchups_page(results)

    tagged = copy.deepcopy(bundle)
    _tag(kind, tagged["matchups"][0]["entrants"][0])  # explicitly tagged
    tagged["matchups"][0]["entrants"][0]["objective_pass_rate"] = 0.123456
    (results / "clean.json").write_text(json.dumps(tagged))
    page = ort.site.build_matchups_page(results)

    dropped = tagged["matchups"][0]["id"]
    assert dropped not in [m["id"] for m in page["matchups"]]
    assert [m for m in page["matchups"]] == [
        m for m in clean_page["matchups"] if m["id"] != dropped
    ]
    assert "0.123456" not in json.dumps(page)
