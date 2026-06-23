"""Issue-level acceptance lock for SOLIDWORKS LEO vs MakerBench DFM (#85)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from makerbench_core import score_file


ROOT = Path(__file__).resolve().parents[1]
LEO_DOC = ROOT / "docs" / "LEO_DFM_COMPARISON.md"
MAPPING = ROOT / "docs" / "leo_dfm_mapping.json"
DFM_RULES = ROOT / "docs" / "DFM_RULES.md"
EXAMPLE_DIR = ROOT / "examples" / "leo_comparison"


def _mapping() -> dict:
    return json.loads(MAPPING.read_text(encoding="utf-8"))


def _rule_ids() -> set[str]:
    return set(re.findall(r"(?m)^\|\s*([A-F][0-9]{1,2})\s*\|", DFM_RULES.read_text()))


def test_issue_85_maps_leo_capabilities_to_public_dfm_rules():
    doc = LEO_DOC.read_text(encoding="utf-8")
    mapping = _mapping()
    defined_rules = _rule_ids()

    assert mapping["schema_version"] == "leo-dfm-mapping-v1"
    assert mapping["source_doc"] == "docs/LEO_DFM_COMPARISON.md"
    assert mapping["rules_doc"] == "docs/DFM_RULES.md"
    assert len(mapping["capabilities"]) >= 6

    expected_capabilities = {
        "Design-error repair and validation",
        "Manufacturing limits / unmachinable features",
        "Over-tight tolerances / fits",
        "Sheet-metal manufacturability",
        "Assembly structure and sequence",
        "STEP and image-to-geometry workflows",
        "Simulation assistance",
    }
    assert {cap["leo_capability"] for cap in mapping["capabilities"]} == expected_capabilities

    for capability in mapping["capabilities"]:
        assert capability["leo_capability"] in doc
        assert capability["makerbench_rules"]
        assert set(capability["makerbench_rules"]) <= defined_rules
        assert capability["coverage"] in {"strong", "partial", "limited"}
        assert capability["gap"]


def test_solidworks_channel_is_optional_local_and_never_core():
    doc = LEO_DOC.read_text(encoding="utf-8")
    normalized = " ".join(doc.split())
    mapping = _mapping()

    assert mapping["leo_is_a_makerbench_grader"] is False
    assert mapping["solidworks_is_a_core_dependency"] is False
    assert mapping["channel"] == "optional-local"

    for required_phrase in (
        "optional-local output channel",
        "core profile must not import SOLIDWORKS APIs",
        "require a SOLIDWORKS license",
        "not a MakerBench grader",
        "agent-under-test channel",
    ):
        assert required_phrase in normalized


def test_minimal_comparison_protocol_keeps_leo_metadata_public_safe():
    doc = LEO_DOC.read_text(encoding="utf-8")
    normalized = " ".join(doc.split())

    for phrase in (
        "Record only public metadata",
        "Do not copy proprietary prompts",
        "hidden rules",
        "vendor logs",
        "exported artifact hash",
        "Never include private oracle geometry",
    ):
        assert phrase in normalized

    for verdict_pair in ("pass | pass", "flag | fail", "flag | pass", "pass | fail"):
        assert verdict_pair in doc


def test_worked_example_is_reproducible_without_solidworks():
    step = EXAMPLE_DIR / "leo_passed_bracket.step"
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")

    assert step.is_file()
    assert "no proprietary SOLIDWORKS internals" in step.read_text(encoding="utf-8")
    result = score_file(step)
    assert result.passed is True
    assert result.makerbench_dfm_score == 100.0
    assert result.input["format"] == "step"

    assert "Agreement matrix" in readme
    assert "LEO verdict column is **recorded metadata only**" in readme
    assert "no SOLIDWORKS license" in readme


def test_canonical_docs_link_the_leo_comparison_path():
    for relative_path in (
        "docs/LANDSCAPE.md",
        "docs/DFM_RULES.md",
        "docs/MAKERBENCH_CORE.md",
        "docs/ROADMAP.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "LEO_DFM_COMPARISON.md" in text, relative_path
