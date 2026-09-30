"""objective_scoreline.json: every failed sub-score is explained (#903), and old files still validate."""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts, _normalize_gate_result

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "objective_scoreline.schema.json").read_text())


def _check(value, schema, path="$"):
    """Tiny validator for the subset this schema uses (no jsonschema dependency)."""
    if "$ref" in schema:
        schema = SCHEMA["definitions"][schema["$ref"].rsplit("/", 1)[1]]
    errors = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        ok = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
              "null": type(None)}
        if not any(isinstance(value, ok[k]) and not (k in ("integer", "number") and isinstance(value, bool))
                   for k in kinds):
            errors.append(f"{path}: {type(value).__name__} is not {kinds}")
            return errors
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += _check(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += _check(item, schema["items"], f"{path}[{i}]")
    return errors


def _doc(rows):
    return {"schema": "makerbench-code-cad-objective-scoreline-v1", "rows": rows}


def test_explained_fixture_validates_and_every_failure_is_explained():
    rows = json.loads((ROOT / "tests/fixtures/objective_scoreline_failed_checks.json").read_text())
    assert _check(_doc(rows), SCHEMA) == []
    failures = [f for r in rows for f in r["failed_checks"]]
    assert failures
    for f in failures:
        assert f["measured"] is not None and f["threshold"] is not None and f["body_id"], f


def test_committed_scorelines_from_before_the_change_still_validate():
    files = glob.glob(str(ROOT / "docs/showcase/**/*scoreline*.json"), recursive=True)
    assert files
    for path in files:
        assert _check(json.loads(Path(path).read_text()), SCHEMA) == [], path


def test_schema_rejects_a_failure_without_its_explanation():
    bad = _doc([{"entrant": "e", "backend": "openscad", "objective_pass_rate": 0.5, "n_objective_trials": 1,
                 "failed_checks": [{"check": "min_wall"}]}])
    errors = _check(bad, SCHEMA)
    assert any("missing 'threshold'" in e for e in errors) and any("missing 'body_id'" in e for e in errors)


def _gate_payload(tmp_path, mesh, spec):
    stl = tmp_path / "output.stl"
    mesh.export(stl.as_posix())
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    ctx = ObjectiveContext(trial_id="t", model_id="m", instrument_id="i", seed=0,
                           scad_path=tmp_path / "x.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    return _normalize_gate_result(runner.mesh_objective_gate(spec)(ctx))


def _by_check(payload):
    return {f["check"]: f for f in payload["failures"]}


def test_each_gate_failure_carries_measured_threshold_and_body(tmp_path):
    thin = trimesh.creation.box(extents=[30, 30, 0.4])  # thin wall, small volume
    spec = {"id": "x", "envelope_mm": [10, 10, 10], "min_bodies": 3, "min_wall_mm": 1.0}
    payload = _gate_payload(tmp_path, thin, spec)
    got = _by_check(payload)
    assert set(got) == {"nonzero_volume", "fits_envelope", "min_wall", "body_count"}
    wall = got["min_wall"]
    assert wall["measured"] == pytest.approx(0.4, abs=0.05) and wall["threshold"] == 1.0
    assert wall["body_id"] == "body_0" and wall["unit"] == "mm" and wall["tolerance"] == 0.05
    assert got["body_count"]["measured"] == 1 and got["body_count"]["threshold"] == 3
    assert got["fits_envelope"]["measured"] == 30.0 and got["fits_envelope"]["threshold"] == 15.0
    assert got["fits_envelope"]["body_id"] == "assembly"
    assert got["nonzero_volume"]["threshold"] == 1000.0 and got["nonzero_volume"]["body_id"] == "body_0"


def test_a_passing_design_has_no_failures(tmp_path):
    payload = _gate_payload(tmp_path, trimesh.creation.box(extents=[30, 30, 30]),
                            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1})
    assert payload["failures"] == [] and payload["passed"] is True


def test_no_watertight_body_says_no_wall_was_measured(tmp_path, monkeypatch):
    from makerbench import geometry

    monkeypatch.setattr(geometry, "is_watertight", lambda _mesh: False)
    payload = _gate_payload(tmp_path, trimesh.creation.box(extents=[30, 30, 30]),
                            {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1})
    got = _by_check(payload)
    assert got["min_wall"]["measured"] is None and got["min_wall"]["body_id"] is None
    assert "no watertight body" in got["min_wall"]["detail"]
    assert got["watertight"]["measured"] == 1 and got["watertight"]["body_id"] == "body_0"
    assert got["watertight"]["body_ids"] == ["body_0"]


def test_scoreline_rows_carry_failed_checks_only_when_something_failed(tmp_path):
    ok = _gate_payload(tmp_path, trimesh.creation.box(extents=[30, 30, 30]),
                       {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1})
    sub = tmp_path / "b"
    sub.mkdir()
    bad = _gate_payload(sub, trimesh.creation.box(extents=[30, 30, 0.4]),
                        {"id": "x", "envelope_mm": [100, 100, 100], "min_bodies": 1})
    def trial(tid, model, objective):
        return {"trial_id": tid, "model_id": model, "instrument_id": "x", "seed": 0, "status": "scored",
                "result": {"objective": objective}}
    rows = runner.collect_objective_scoreline({"trials": [trial("a", "good", ok), trial("b", "bad", bad)]})
    by = {r["entrant"]: r for r in rows}
    assert "failed_checks" not in by["good"]
    assert by["bad"]["failed_checks"][0]["trial_id"] == "b"
    assert by["bad"]["failed_checks"][0]["instrument_id"] == "x"
    assert _check(_doc(rows), SCHEMA) == []


def test_older_results_without_explanations_are_listed_not_dropped():
    old = {"trial_id": "t", "model_id": "m", "instrument_id": "x", "seed": 1, "status": "scored",
           "result": {"objective": {"objective_pass_rate": 0.8333, "passed": False,
                                    "sub_scores": {"renders": 1.0, "min_wall": 0.0}}}}
    (row,) = runner.collect_objective_scoreline({"trials": [old]})
    (failed,) = row["failed_checks"]
    assert failed["check"] == "min_wall" and failed["measured"] is None and "predates" in failed["detail"]
    assert _check(_doc([row]), SCHEMA) == []
