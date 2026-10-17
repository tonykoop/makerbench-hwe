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
import re
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


# ---------------------------------------------------------------- raw-access guard

RAW_TRIAL_ACCESS = re.compile(
    r"""\[\s*["']trials["']\s*\]|\.get\(\s*["']trials["']|setdefault\(\s*["']trials["']"""
)

# Every raw read of a run log's trial rows must go through ``run_log_io.trial_rows``
# (the blind-series reader that applies track isolation). The only exceptions are listed
# here as (file, exact stripped source line) with the reason it must see every row.
_WRITER = "writer/merger: must preserve and update every row of the log"
_LOOKUP = "by-trial_id lookup on one named row, not an aggregate"
_OTHER_DATA = "not a run log: studio showcase/demo data with its own 'trials' key"
_DERIVED = "reads the already-filtered summary built from trial_rows"
RAW_TRIAL_ALLOWLIST: dict[str, dict[str, str]] = {
    "makerbench/run_log_io.py": {
        'or publishes trials must iterate this, never ``run_log["trials"]``;': "docstring",
        'return [row for row in run_log.get("trials") or [] if not _off_track(row, config)]': "the helper itself",
    },
    "makerbench/nightly_cad.py": {
        'return any(row.get("trial_id") == expected for row in payload.get("trials") or [])': _LOOKUP,
        '(item for item in payload.get("trials") or [] if item.get("trial_id") == expected),': _LOOKUP,
        'if any(row.get("trial_id") == trial_id for row in payload.get("trials") or []):': _LOOKUP,
        'payload.setdefault("trials", []).append(': _WRITER,
    },
    "makerbench/code_cad_arena_runner.py": {
        'if any(t.get("trial_id") == trial_id for t in log.get("trials") or []):': _LOOKUP,
        'log.setdefault("trials", []).append(entry)': _WRITER,
    },
    "makerbench/arena_studio/demo.py": {
        'if isinstance(node.get("trials"), list):': "packaged showcase rows, not a run log",
        'node["trials"] = [_without_retired(t) for t in node["trials"]]': "packaged showcase rows, not a run log",
    },
    "makerbench/code_cad_orchestrator.py": {
        'existing = {entry["trial_id"]: entry for entry in log["trials"]}': _WRITER,
        'payload.setdefault("trials", [])': _WRITER,
        'payload["trials"] = merge_trial_rows(payload["trials"], managed)': _WRITER,
    },
    "makerbench/arena_studio/workbench.py": {
        'trial = next((t for t in log.get("trials", []) if t.get("trial_id") == trial_id), None)': _LOOKUP,
    },
    "makerbench/arena_studio/service.py": {
        'matches = [row for row in log.get("trials") or [] if row.get("trial_id") == trial_id]': _LOOKUP,
        'trials_by_id = {str(t.get("trial_id")): t for t in run_log.get("trials") or []}': _LOOKUP,
        'trials = summary.get("trials") or []': _DERIVED,
    },
    "scripts/regrade_scoreline.py": {
        'regraded["trials"] = copy.deepcopy(trial_rows(log))': "assigns the already-filtered rows",
        'for trial in regraded.get("trials") or []:': "write-back over the already-filtered copy, by trial_id",
    },
    "scripts/build_studio_demo_data.py": {
        'trials = row.get("trials", [])': _OTHER_DATA,
        'for trial in record["trials"]': _OTHER_DATA,
        'row["trials"] = [{"seed": trial["seed"], "image": image(trial["image"]),': _OTHER_DATA,
        'row["image"] = row["trials"][0]["image"]': _OTHER_DATA,
    },
    "scripts/build_studio_demo_space.py": {
        'trials = row.get("trials", [])': _OTHER_DATA,
        'for trial in row.get("trials", []):': _OTHER_DATA,
    },
}


def _raw_trial_lines(source_dirs=("makerbench", "scripts", "site")):
    found = []
    for directory in source_dirs:
        for path in sorted((ROOT / directory).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if RAW_TRIAL_ACCESS.search(line):
                    found.append((rel, number, line.strip()))
    return found


def test_no_raw_trial_access_outside_the_allowlist():
    """A new reader of run-log trials must use run_log_io.trial_rows, or be justified here."""
    offenders = [
        f"{rel}:{number}: {line}"
        for rel, number, line in _raw_trial_lines()
        if line not in RAW_TRIAL_ALLOWLIST.get(rel, {})
    ]
    assert not offenders, (
        "raw run-log trial access; iterate makerbench.run_log_io.trial_rows(run_log) "
        "(track isolation), or add a justified entry to RAW_TRIAL_ALLOWLIST:\n"
        + "\n".join(offenders)
    )


def test_raw_trial_allowlist_has_no_stale_entries():
    present = {(rel, line) for rel, _number, line in _raw_trial_lines()}
    stale = [
        f"{rel}: {line}"
        for rel, lines in RAW_TRIAL_ALLOWLIST.items()
        for line in lines
        if (rel, line) not in present
    ]
    assert not stale, "stale allowlist entries:\n" + "\n".join(stale)


@pytest.mark.parametrize(
    "line",
    [
        'for t in log["trials"]:',
        "for t in log.get('trials') or []:",
        'log.setdefault("trials", []).append(x)',
        'rows = run_log[ "trials" ]',
    ],
)
def test_guard_pattern_detects_raw_access(line):
    assert RAW_TRIAL_ACCESS.search(line)
    assert not RAW_TRIAL_ACCESS.search("for t in trial_rows(log):")
