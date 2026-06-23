"""Issue-level acceptance lock for the makerbench-core component score (#80)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from makerbench.adapters import AssemblyPart, CADCLAWAdapter
from makerbench_core import score_file
from makerbench_core.cli import main


ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "makerbench-core"
DOC = ROOT / "docs" / "MAKERBENCH_CORE.md"
STEP = ROOT / "examples" / "closed_loop_instrument_bridge.step"

HEAVY_DEPS = (
    "trimesh",
    "manifold3d",
    "rtree",
    "scipy",
    "networkx",
    "numpy",
    "shapely",
    "pydantic",
    "typer",
    "rich",
)


def test_standalone_distribution_declares_pip_install_surface_and_cli():
    pyproject = (PKG / "pyproject.toml").read_text(encoding="utf-8")
    readme = (PKG / "README.md").read_text(encoding="utf-8")

    assert 'name = "makerbench-core"' in pyproject
    assert 'version = "0.1.0"' in pyproject
    assert "dependencies = []" in pyproject
    assert 'makerbench-dfm-score = "makerbench_core.cli:main"' in pyproject
    assert '"../../makerbench_core" = "makerbench_core"' in pyproject
    assert "pip install makerbench-core" in readme


def test_python_api_scores_public_step_with_stable_structured_output():
    first = score_file(STEP)
    second = score_file(STEP)

    assert first == second
    assert first.profile == "portable-dfm-v1"
    assert first.score_algorithm == "weighted_pass_fraction_v1"
    assert first.makerbench_dfm_score == 100.0
    assert first.passed is True

    payload = first.to_dict()
    assert payload["schema_version"] == "makerbench-core-dfm-score-v1"
    assert payload["input"]["format"] == "step"
    assert len(payload["input"]["sha256"]) == 64
    assert payload["reproducibility"] == {
        "version_pin": "makerbench-core==0.1.0",
        "profile": "portable-dfm-v1",
        "score_algorithm": "weighted_pass_fraction_v1",
        "oracle_access": False,
        "llm_judge": False,
    }
    assert {rule["id"] for rule in payload["rules"]} >= {
        "integrity.public_surface",
        "format.step.iso_10303",
        "format.step.sections",
        "format.step.product_metadata",
        "geometry.step_shape_tokens",
    }


def test_cli_emits_json_human_output_and_fail_under_status(capsys, tmp_path):
    assert main([str(STEP), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["makerbench_dfm_score"] == 100.0

    assert main([str(STEP)]) == 0
    human = capsys.readouterr().out
    assert "makerbench_dfm_score: 100.0%" in human
    assert "profile: portable-dfm-v1" in human

    invalid = tmp_path / "bad.step"
    invalid.write_text("not really a STEP file", encoding="utf-8")
    assert main([str(invalid), "--fail-under", "90"]) == 1


def test_core_import_and_scoring_do_not_pull_heavy_harness_dependencies():
    code = (
        "import sys\n"
        "from makerbench_core import score_file\n"
        "score_file(sys.argv[1])\n"
        f"heavy = [m for m in {HEAVY_DEPS!r} if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(STEP)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert proc.stdout.strip() == ""


def test_private_oracle_paths_fail_the_public_surface_rule(tmp_path):
    artifact = tmp_path / "private" / "oracles" / "candidate.step"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(STEP.read_text(encoding="utf-8"), encoding="utf-8")

    result = score_file(artifact)
    public_surface = next(rule for rule in result.rules if rule.id == "integrity.public_surface")

    assert public_surface.passed is False
    assert result.passed is False


def test_cadclaw_adapter_uses_same_score_file_entrypoint(tmp_path):
    del tmp_path

    grade = CADCLAWAdapter().evaluate(
        [AssemblyPart("bridge", str(STEP), quantity=1)],
        assembly_id="instrument-bridge",
    )
    payload = grade.to_dict()

    assert payload["provenance"]["scorer"] == "makerbench_core.score_file"
    assert payload["parts"][0]["makerbench_dfm_score"] == 100.0
    assert payload["assembly_score"] == 100.0
    assert payload["gate_passed"] is True


def test_docs_pin_reproducibility_and_external_leaderboard_integration():
    doc = DOC.read_text(encoding="utf-8")

    for phrase in (
        "pip install makerbench-core",
        "makerbench-dfm-score candidate.step --json",
        "zero runtime dependencies",
        "makerbench_dfm_score",
        "makerbench-core==0.1.0, profile=portable-dfm-v1",
        "External Leaderboard Example",
        "score_file(Path(artifact_path))",
    ):
        assert phrase in doc
