"""Guard for the Workflow Track RFC document structure (mb#87).

docs/WORKFLOW_TRACK.md is the design contract that makes the workflow-track
safe to run alongside the autonomous leaderboard. These tests pin its required
sections so a future editor cannot quietly drop the league-separation argument,
the taxonomy, or the trust model.
"""

from __future__ import annotations

from pathlib import Path

DOC = Path("docs/WORKFLOW_TRACK.md")


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_workflow_track_doc_exists():
    assert DOC.is_file(), "docs/WORKFLOW_TRACK.md must exist (mb#87 acceptance)"


def test_workflow_track_has_all_ten_sections():
    text = _text()
    required_headings = [
        "## 1. The architectural unlock",
        "## 2. The science",
        "## 3. Taxonomy",
        "## 4. The trust model",
        "## 5. The `WorkflowManifest`",
        "## 6. Anti-gaming",
        "## 7. Leaderboard and site",
        "## 8. Quarterly domain tracks",
        "## 9. Integrity",
        "## 10. Implementation phases",
    ]
    missing = [h for h in required_headings if h not in text]
    assert missing == [], f"Missing RFC sections: {missing}"


def test_league_separation_is_non_negotiable_is_stated():
    """The core scientific principle must be explicit in the RFC."""
    text = _text()
    assert "non-negotiable" in text, (
        "RFC must state that league separation is non-negotiable (mb#87)"
    )


def test_taxonomy_table_has_required_columns():
    """The harness_class taxonomy table must define all four columns."""
    text = _text()
    required_cols = [
        "`harness_class`",
        "`harness_subclass`",
        "What it is",
        "Examples",
    ]
    missing = [c for c in required_cols if c not in text]
    assert missing == [], f"Taxonomy table missing columns: {missing}"


def test_taxonomy_rows_cover_all_three_subclasses():
    """Every declared HarnessSubclass must appear in the RFC taxonomy."""
    text = _text()
    subclasses = [
        "`api-driven-code`",
        "`gui-injected-copilot`",
        "`whole-canvas-diffusion-code`",
    ]
    missing = [s for s in subclasses if s not in text]
    assert missing == [], f"RFC taxonomy missing subclass rows: {missing}"


def test_rfc_references_tracking_epic():
    """The RFC must link back to the tracking epic (#100)."""
    text = _text()
    assert "#100" in text, "RFC must reference tracking epic #100"
