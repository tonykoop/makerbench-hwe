"""Synthetic metadata fixtures; no provider, real run directory, or geometry needed."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "frontier_replay"
SCRIPT = ROOT / "scripts" / "build_frontier_replay.py"
spec = importlib.util.spec_from_file_location("build_frontier_replay", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def runs(tmp_path):
    run = tmp_path / "runs" / "round1"
    run.mkdir(parents=True)
    (run / "run_log.json").write_bytes((FIXTURES / "run_log.json").read_bytes())
    for i in range(3):
        gen = run / "gen" / f"fixture-{i}"
        gen.mkdir(parents=True)
        # Opaque hash input, not a benchmark solution or executable CAD.
        (gen / "placeholder.scad").write_bytes(b"synthetic source placeholder\n")
    return run.parent


def cli(runs, out, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--runs", str(runs), "--prior-snapshot",
         str(FIXTURES / "prior.json"), "--recording", str(FIXTURES / "recording.json"),
         "--out", str(out), *extra], capture_output=True, text=True,
    )


def test_fixture_round_trip(runs, tmp_path):
    out = tmp_path / "replay.json"
    result = cli(runs, out)
    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == (FIXTURES / "expected.json").read_bytes()
    bundle = json.loads(out.read_text())
    assert "regrade" not in bundle
    prior = load("prior.json")
    for key in ("canary", "harness_git_revision", "harness_file_sha256", "openscad_version",
                "verification_status"):
        assert bundle[key] == prior[key]
    assert [m["entrants"][1] for m in bundle["matchups"]] == [
        m["entrants"][1] for m in prior["matchups"]]
    assert "synthetic error" not in out.read_text()
    assert str(runs) not in out.read_text()


def test_standalone_cli_without_pythonpath(runs, tmp_path, monkeypatch):
    monkeypatch.delenv('PYTHONPATH', raising=False)
    out = tmp_path / 'standalone.json'
    result = cli(runs, out)
    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == (FIXTURES / 'expected.json').read_bytes()


def test_regrade_absent_vs_present(runs, tmp_path):
    log = runs / "round1" / "run_log.json"
    provenance = dict(regraded_by="fixture reviewer", regrade_commit="fixture-commit",
                      gate_policy="fixture-policy", source_run_log_sha256={
                          "round1/run_log.json": hashlib.sha256(log.read_bytes()).hexdigest()})
    path = tmp_path / "regrade.json"
    path.write_text(json.dumps(provenance))
    out = tmp_path / "replay.json"
    result = cli(runs, out, "--regrade", str(path))
    assert result.returncode == 0, result.stderr
    bundle = json.loads(out.read_text())
    assert bundle.pop("regrade") == provenance
    assert bundle == load("expected.json")
    provenance["source_run_log_sha256"]["round1/run_log.json"] = "0" * 64
    with pytest.raises(ValueError, match="does not match consumed logs"):
        mod.build(runs, load("prior.json"), load("recording.json"), provenance)


@pytest.mark.parametrize("relative", ["output.json", "round1/run_log.json"])
def test_refuses_output_inside_runs(runs, relative):
    before = (runs / "round1" / "run_log.json").read_bytes()
    result = cli(runs, runs / relative)
    assert result.returncode != 0
    assert "--out must be outside --runs" in result.stderr
    assert (runs / "round1" / "run_log.json").read_bytes() == before


def test_refuses_output_through_symlink(runs, tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(runs, target_is_directory=True)
    assert cli(runs, link / "output.json").returncode != 0
    assert not (runs / "output.json").exists()


@pytest.mark.parametrize("change", ["duplicate", "missing", "unfinished"])
def test_rejects_incomplete_or_ambiguous_logs(runs, change):
    path = runs / "round1" / "run_log.json"
    log = json.loads(path.read_text())
    if change == "duplicate":
        log["trials"].append(log["trials"][0])
    elif change == "missing":
        log["trials"].pop()
    else:
        log["trials"][0]["status"] = "pending"
    path.write_text(json.dumps(log))
    with pytest.raises((ValueError, KeyError)):
        mod.build(runs, load("prior.json"), load("recording.json"))


@pytest.mark.parametrize("extra_key", ["verification_status", "objective_pass_rate", "unexpected"])
def test_rejects_extra_regrade_keys(runs, tmp_path, extra_key):
    log = runs / "round1" / "run_log.json"
    provenance = dict(regraded_by="fixture reviewer", regrade_commit="fixture-commit",
                      gate_policy="fixture-policy", source_run_log_sha256={
                          "round1/run_log.json": hashlib.sha256(log.read_bytes()).hexdigest()})
    provenance[extra_key] = "untrusted override"
    with pytest.raises(ValueError, match=f"unsupported keys: {extra_key}"):
        mod.build(runs, load("prior.json"), load("recording.json"), provenance)
    path = tmp_path / "regrade.json"
    path.write_text(json.dumps(provenance))
    out = tmp_path / "refused.json"
    result = cli(runs, out, "--regrade", str(path))
    assert result.returncode != 0
    assert f"unsupported keys: {extra_key}" in result.stderr
    assert not out.exists()


def test_carries_sorted_primary_borderline_checks(runs, tmp_path):
    path = runs / "round1" / "run_log.json"
    log = json.loads(path.read_text())
    objective = log["trials"][0]["result"]["objective"]
    objective["sub_scores"].update(z_wall="borderline", a_wall="borderline")
    # A separate diagnostic must not contribute to the primary check count.
    log["trials"][0]["result"]["advisory"] = {"sub_scores": {"outside": "borderline"}}
    path.write_text(json.dumps(log))
    out = tmp_path / "borderline.json"
    result = cli(runs, out)
    assert result.returncode == 0, result.stderr
    bundle = json.loads(out.read_text())
    expected = load("expected.json")
    expected["matchups"][0]["entrants"][0]["borderline_checks"] = ["a_wall", "z_wall"]
    assert bundle == expected  # rates, failures, other rows and provenance unchanged
    row = bundle["matchups"][0]["entrants"][0]
    assert row["failed_checks"] == {"second": 1}
    assert row["objective_pass_rate"] == 0.5
    assert len(row["borderline_checks"]) == 2  # checks, not borderline trials
    assert all("borderline_checks" not in matchup["entrants"][0]
               for matchup in bundle["matchups"][1:])


def test_nonprimary_borderline_is_not_promoted(runs):
    path = runs / "round1" / "run_log.json"
    log = json.loads(path.read_text())
    log["trials"][0]["result"]["advisory"] = {"sub_scores": {"min_wall": "borderline"}}
    path.write_text(json.dumps(log))
    bundle = mod.build(runs, load("prior.json"), load("recording.json"))
    assert bundle == load("expected.json")
    assert all("borderline_checks" not in row
               for matchup in bundle["matchups"] for row in matchup["entrants"])
