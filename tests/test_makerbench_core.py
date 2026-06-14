"""Tests for the lightweight makerbench-core DFM component score."""

from __future__ import annotations

import json
from pathlib import Path

from makerbench_core import score_file
from makerbench_core.cli import main


MINIMAL_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('MakerBench test STEP'), '2;1');
FILE_NAME('candidate.step', '2026-06-14T00:00:00', (), (), 'makerbench-core', '', '');
FILE_SCHEMA(('AP214'));
ENDSEC;
DATA;
#1 = PRODUCT('plate', 'plate', '', (#2));
#2 = PRODUCT_CONTEXT('', #3, '');
#3 = APPLICATION_CONTEXT('mechanical design');
#4 = CARTESIAN_POINT('', (0.0, 0.0, 0.0));
#5 = PRODUCT_DEFINITION('design', '', #6, #7);
#6 = PRODUCT_DEFINITION_FORMATION('', '', #1);
#7 = PRODUCT_DEFINITION_CONTEXT('', #3, 'design');
ENDSEC;
END-ISO-10303-21;
"""


def test_score_file_returns_stable_step_json(tmp_path):
    artifact = tmp_path / "candidate.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")

    result = score_file(artifact)
    payload = result.to_dict()

    assert payload["schema_version"] == "makerbench-core-dfm-score-v1"
    assert payload["profile"] == "portable-dfm-v1"
    assert payload["score_algorithm"] == "weighted_pass_fraction_v1"
    assert payload["makerbench_dfm_score"] == 100.0
    assert payload["passed"] is True
    assert payload["input"]["format"] == "step"
    assert len(payload["input"]["sha256"]) == 64
    assert payload["reproducibility"]["oracle_access"] is False
    assert payload["reproducibility"]["llm_judge"] is False
    assert {rule["id"] for rule in payload["rules"]} >= {
        "format.step.iso_10303",
        "format.step.sections",
        "format.step.product_metadata",
        "geometry.step_shape_tokens",
    }


def test_score_file_penalizes_invalid_step(tmp_path):
    artifact = tmp_path / "candidate.step"
    artifact.write_text("not really a STEP file", encoding="utf-8")

    result = score_file(artifact)
    failed = {rule.id for rule in result.rules if not rule.passed}

    assert result.makerbench_dfm_score < 100.0
    assert result.passed is False
    assert "format.step.iso_10303" in failed
    assert "format.step.sections" in failed


def test_score_file_rejects_private_oracle_paths(tmp_path):
    artifact = tmp_path / "private" / "oracles" / "candidate.step"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")

    result = score_file(artifact)
    public_surface = next(rule for rule in result.rules if rule.id == "integrity.public_surface")

    assert public_surface.passed is False
    assert result.passed is False


def test_score_file_supports_openscad_without_importing_full_harness(tmp_path):
    artifact = tmp_path / "candidate.scad"
    artifact.write_text("difference() { cube([10, 10, 2]); cylinder(r=2, h=4); }", encoding="utf-8")

    result = score_file(artifact)

    assert result.input["format"] == "openscad"
    assert result.passed is True


def test_cli_emits_json_and_human_output(tmp_path, capsys):
    artifact = tmp_path / "candidate.step"
    artifact.write_text(MINIMAL_STEP, encoding="utf-8")

    assert main([str(artifact), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["makerbench_dfm_score"] == 100.0

    assert main([str(artifact)]) == 0
    text = capsys.readouterr().out
    assert "makerbench_dfm_score: 100.0%" in text


def test_cli_fail_under_returns_nonzero(tmp_path):
    artifact = tmp_path / "candidate.step"
    artifact.write_text("not really a STEP file", encoding="utf-8")

    assert main([str(artifact), "--fail-under", "90"]) == 1


def test_pyproject_exposes_core_package_and_script():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "makerbench_core" in pyproject
    assert 'makerbench-dfm-score = "makerbench_core.cli:main"' in pyproject
