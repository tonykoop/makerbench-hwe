"""Exported-artifact evaluator plugin manifest contract tests."""

from __future__ import annotations

from pathlib import Path

from makerbench.evaluators import (
    builtin_evaluator_manifest,
    is_evaluator_manifest_valid,
    load_evaluator_manifest,
    validate_evaluator_manifest,
)
from makerbench.schema import EvaluatorManifest, EvaluatorSpec

ROOT = Path(__file__).resolve().parents[1]


def _spec(**overrides) -> EvaluatorSpec:
    base = {
        "name": "mesh_watertight_volume",
        "version": "0.1",
        "artifact_formats": ["stl"],
        "contributes_levels": [1, 2, 3],
    }
    base.update(overrides)
    return EvaluatorSpec(**base)


def test_builtin_manifest_is_empty_but_valid():
    manifest = builtin_evaluator_manifest()

    # Core grading is not yet a registered plugin: the registry exists, empty.
    assert manifest.evaluators == []
    assert validate_evaluator_manifest(manifest) == []
    assert is_evaluator_manifest_valid(manifest)


def test_example_manifest_loads_and_validates_clean():
    manifest = load_evaluator_manifest(
        str(ROOT / "examples" / "evaluator_manifest.example.json")
    )

    # Candidate evaluators span the required exported-artifact domains plus the
    # optional-local STEP/B-rep topology scaffold.
    formats = {fmt for ev in manifest.evaluators for fmt in ev.artifact_formats}
    assert {"svg", "dxf", "stl", "step", "json", "gcode", "inp"}.issubset(formats)
    runtimes = {ev.runtime for ev in manifest.evaluators}
    assert {"public_ci", "optional_local"}.issubset(runtimes)
    assert validate_evaluator_manifest(manifest) == []


def test_example_manifest_declares_brep_topology_as_optional_local():
    manifest = load_evaluator_manifest(
        str(ROOT / "examples" / "evaluator_manifest.example.json")
    )

    by_name = {ev.name: ev for ev in manifest.evaluators}
    brep = by_name["step_brep_topology"]
    assert brep.runtime == "optional_local"
    assert brep.artifact_formats == ["step", "stp"]
    assert brep.dependencies == ["build123d"]
    assert "solid_count" in brep.metrics


def test_private_evaluator_in_public_manifest_is_rejected():
    manifest = EvaluatorManifest(evaluators=[_spec(visibility="private")])
    problems = validate_evaluator_manifest(manifest)

    assert any("must list only public evaluators" in p for p in problems)


def test_level_outside_the_four_failure_levels_is_rejected():
    manifest = EvaluatorManifest(evaluators=[_spec(contributes_levels=[2, 5])])
    problems = validate_evaluator_manifest(manifest)

    assert any("outside the four" in p for p in problems)


def test_duplicate_name_and_missing_fields_are_rejected():
    manifest = EvaluatorManifest(
        evaluators=[
            _spec(name="dup"),
            _spec(name="dup", version="", artifact_formats=[]),
        ]
    )
    problems = validate_evaluator_manifest(manifest)

    assert any("duplicate evaluator name" in p for p in problems)
    assert any("version is required" in p for p in problems)
    assert any("at least one artifact_format is required" in p for p in problems)
