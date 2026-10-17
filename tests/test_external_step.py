"""External intake uses generated public geometry, never vendor/answer files."""

import hashlib
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from makerbench import external_step as intake
from makerbench import step_mesh
from makerbench.code_cad_arena_runner import build_vote_candidates, collect_objective_scoreline
from makerbench.render import CompileError
from test_step_mesh import make_step, real_step  # noqa: F401


def metadata(content=b"synthetic"):
    return {
        "entrant_id": "example-export", "task_id": "example", "seed": 0,
        "tool_name": "OCP test exporter", "tool_version": "7.9.3.1",
        "production_method": "agent", "production_notes": "Generated public box fixture",
        "started_at": "2026-10-16T00:00:00Z", "exported_at": "2026-10-16T00:01:00Z",
        "source_path": "results/example-export/artifacts/example_seed0_external_step.step",
        "source_sha256": hashlib.sha256(content).hexdigest(),
    }


REGISTRY = {"instruments": [{"id": "example", "envelope_mm": [100, 100, 100],
                             "min_bodies": 1}]}


@pytest.mark.parametrize("field,value", [
    ("tool_name", " "), ("tool_version", ""), ("production_method", "unknown"),
    ("started_at", "2026-10-16T00:00:00"), ("exported_at", "2026-10-15T00:00:00Z"),
    ("source_sha256", "bogus"), ("seed", 3), ("track", "blind"),
    ("modality", "code_cad"), ("entrant_type", "model"),
    ("source_path", "results/x/artifacts/../../example.step"),
    ("source_path", "/tmp/example.step"), ("source_path", "results/x/artifacts/x.py"),
])
def test_invalid_provenance_rejected(field, value):
    with pytest.raises(ValidationError):
        intake.ExternalStepSubmission(**{**metadata(), field: value})


def test_future_timestamps_rejected_with_small_skew(monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(intake, "_utcnow", lambda: now)

    def stamp(delta):
        return (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    base = {**metadata(), "started_at": stamp(timedelta(minutes=-10))}
    # within the skew: accepted; just past it: rejected, on either timestamp
    intake.ExternalStepSubmission(**{**base, "exported_at": stamp(intake.MAX_CLOCK_SKEW)})
    for delta in (intake.MAX_CLOCK_SKEW + timedelta(seconds=1), timedelta(days=1)):
        with pytest.raises(ValidationError, match="future"):
            intake.ExternalStepSubmission(**{**base, "exported_at": stamp(delta)})
        with pytest.raises(ValidationError, match="future"):
            intake.ExternalStepSubmission(
                **{**base, "started_at": stamp(delta), "exported_at": stamp(delta)})


def test_unknown_task_id_is_a_clean_cli_error(tmp_path, capsys):
    source = tmp_path / "source.step"
    source.write_bytes(b"synthetic")
    meta = tmp_path / "submission.json"
    meta.write_text(json.dumps({**metadata(), "task_id": "no_such_task"}))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(REGISTRY))
    argv = [str(meta), str(source), str(tmp_path / "out"), "--registry", str(registry)]
    with pytest.raises(ValueError, match="unknown task id 'no_such_task'"):
        intake.intake_step(intake.ExternalStepSubmission(**{**metadata(), "task_id": "no_such_task"}),
                           source, tmp_path / "out2", registry=REGISTRY)
    proc = subprocess.run([sys.executable, "-m", "makerbench.external_step", *argv],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "external STEP: unknown task id 'no_such_task'" in proc.stderr
    assert "Traceback" not in proc.stderr and "KeyError" not in proc.stderr


def test_hash_mismatch_never_calls_converter(tmp_path, monkeypatch):
    source = tmp_path / "source.step"
    source.write_bytes(b"tampered")
    monkeypatch.setattr(step_mesh, "convert_step_to_mesh", lambda *a: pytest.fail("called"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        intake.intake_step(intake.ExternalStepSubmission(**metadata()), source,
                           tmp_path / "out", registry=REGISTRY)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("failure", [CompileError("malformed STEP"),
                                    RuntimeError("sandbox unavailable")])
def test_candidate_failure_vs_environment_failure(tmp_path, monkeypatch, failure):
    source = tmp_path / "source.step"
    source.write_bytes(b"synthetic")
    def fail(snapshot, out):
        assert snapshot != source
        assert snapshot.read_bytes() == source.read_bytes()
        raise failure
    monkeypatch.setattr(step_mesh, "convert_step_to_mesh", fail)
    submission = intake.ExternalStepSubmission(**metadata())
    out = tmp_path / "out"
    if not isinstance(failure, CompileError):
        with pytest.raises(RuntimeError, match="sandbox unavailable"):
            intake.intake_step(submission, source, out, registry=REGISTRY)
        assert not out.exists()
    else:
        result = intake.intake_step(submission, source, out, registry=REGISTRY)
        assert result["status"] == "auto_fail"
        assert result["objective"] is None
        assert result["track"] == "external_step"


@pytest.mark.parametrize("location", ["entry", "result", "meta", "config"])
def test_external_entries_never_blend_into_blind_series(tmp_path, location):
    png = tmp_path / "preview.png"
    png.write_bytes(b"public control")
    base = {"model_id": "same-name", "instrument_id": "example", "seed": 0,
            "rep": 0, "status": "scored", "trial_id": "blind",
            "result": {"render_ok": True, "artifacts": {"png_path": str(png)},
                       "objective": {"objective_pass_rate": 1.0}}}
    external = json.loads(json.dumps(base))
    external["trial_id"] = "external"
    log = {"trials": [base, external]}
    target = external if location == "entry" else (
        log.setdefault("config", {}) if location == "config"
        else external.setdefault(location, {}))
    target["track"] = "external_step"
    rows = collect_objective_scoreline(log)
    pairs = build_vote_candidates(log)
    if location == "config":
        assert rows == [] and pairs == {}
    else:
        assert rows[0]["n_objective_trials"] == 1
        assert [c.trial_id for c in pairs[("example", 0, 0)]] == ["blind"]


@real_step
def test_generated_export_cli_worked_example(make_step, tmp_path):  # noqa: F811
    source = make_step()
    meta = tmp_path / "submission.json"
    meta.write_text(json.dumps(metadata(source.read_bytes())))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(REGISTRY))
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, "-m", "makerbench.external_step", str(meta),
                           str(source), str(out), "--registry", str(registry)],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads((out / "external_step.json").read_text())
    assert result["status"] == "scored"
    assert result["track"] == result["modality"] == result["entrant_type"] == "external_step"
    assert result["confinement"] == "verified"
    assert result["verification_status"] == "unverified"
    assert result["objective"]["sub_scores"]["watertight"] == 1.0
    assert result["objective"]["sub_scores"]["body_count"] == 1.0
    assert result["artifact_sha256"] == hashlib.sha256((out / "output.stl").read_bytes()).hexdigest()
    assert result["dossier"]["artifacts"][0]["sha256"] == metadata(source.read_bytes())["source_sha256"]
    # Separate reports cannot be consumed as a blind run log.
    assert collect_objective_scoreline(result) == []
