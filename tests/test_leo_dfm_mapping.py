"""Machine-readable LEO<->MakerBench mapping + worked comparison (issue #85).

Keeps the structured mapping in sync with the prose note and the public DFM rule
catalog, and proves the worked example actually scores via the deterministic
``makerbench-core`` path (the pass/pass row of the agreement matrix).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from makerbench_core import score_file

ROOT = Path(__file__).resolve().parents[1]
MAPPING = ROOT / "docs" / "leo_dfm_mapping.json"
LEO_DOC = ROOT / "docs" / "LEO_DFM_COMPARISON.md"
DFM_RULES = ROOT / "docs" / "DFM_RULES.md"
EXAMPLE_DIR = ROOT / "examples" / "leo_comparison"
EXAMPLE_STEP = EXAMPLE_DIR / "leo_passed_bracket.step"


def _mapping() -> dict:
    return json.loads(MAPPING.read_text(encoding="utf-8"))


def _defined_rule_ids() -> set[str]:
    text = DFM_RULES.read_text(encoding="utf-8")
    # Rule rows look like "| A1 | ...". Collect the ids that head a table row.
    return set(re.findall(r"(?m)^\|\s*([A-F][0-9]{1,2})\s*\|", text))


def test_mapping_is_well_formed():
    data = _mapping()
    assert data["schema_version"] == "leo-dfm-mapping-v1"
    # The two non-negotiable constraints, as machine-readable flags.
    assert data["leo_is_a_makerbench_grader"] is False
    assert data["solidworks_is_a_core_dependency"] is False
    assert data["channel"] == "optional-local"
    assert data["capabilities"], "mapping must list capabilities"


def test_every_mapped_rule_exists_in_the_catalog():
    defined = _defined_rule_ids()
    assert defined, "could not parse rule ids from DFM_RULES.md"
    levels = set(_mapping()["coverage_levels"])
    families = set(_mapping()["rule_families"])
    for cap in _mapping()["capabilities"]:
        assert cap["leo_capability"]
        assert cap["coverage"] in levels
        assert cap["makerbench_rules"], cap["leo_capability"]
        for rule_id in cap["makerbench_rules"]:
            assert rule_id in defined, f"{rule_id} not defined in DFM_RULES.md"
        for family in cap["rule_families"]:
            assert family in families, f"unknown rule family {family!r}"


def test_mapping_covers_the_capabilities_named_in_the_prose():
    doc = LEO_DOC.read_text(encoding="utf-8")
    for cap in _mapping()["capabilities"]:
        assert cap["leo_capability"] in doc, cap["leo_capability"]


def test_doc_links_the_mapping_and_worked_example():
    doc = LEO_DOC.read_text(encoding="utf-8")
    assert "leo_dfm_mapping.json" in doc
    assert "examples/leo_comparison" in doc


def test_worked_example_scores_as_passing():
    assert EXAMPLE_STEP.is_file()
    result = score_file(EXAMPLE_STEP)
    # The pass/pass agreement-matrix row must be reproducible with no CAD license.
    assert result.passed is True
    assert result.makerbench_dfm_score == 100.0
    assert result.input["format"] == "step"


def test_worked_example_readme_documents_the_agreement_matrix():
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    assert "Agreement matrix" in readme
    assert "makerbench-dfm-score" in readme
    assert "public-safe" in readme
