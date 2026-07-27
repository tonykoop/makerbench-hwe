"""Contract checks for the SOLIDWORKS LEO comparison note."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEO_DOC = ROOT / "docs" / "LEO_DFM_COMPARISON.md"


def test_leo_comparison_doc_covers_issue_85_acceptance_criteria():
    text = LEO_DOC.read_text(encoding="utf-8")

    assert "DFM_RULES.md" in text
    assert "MAKERBENCH_CORE.md" in text
    assert "SOLIDWORKS/LEO channel fits MakerBench only as an" in text
    assert "**optional-local output channel**" in text
    assert "core profile must not import SOLIDWORKS APIs" in text
    assert "not a MakerBench grader" in text

    for rule_family in (
        "3D printing",
        "Sheet-metal",
        "Laser/vector",
        "Catalog/BOM",
        "CNC",
    ):
        assert rule_family.lower().replace("/", "") in text.lower().replace("/", "")


def test_leo_comparison_doc_is_linked_from_canonical_docs():
    for relative_path in (
        "docs/LANDSCAPE.md",
        "docs/DFM_RULES.md",
        "docs/MAKERBENCH_CORE.md",
        "docs/ROADMAP.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "LEO_DFM_COMPARISON.md" in text, relative_path
