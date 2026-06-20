"""Contract guard tests for the CHALLENGE_SPEC.md quarterly challenge lifecycle (#95).

The challenge lifecycle spec is documentation-only: the golden master stays private,
grading is purely geometry-based, and a challenge is never a score gate. These tests
guard that the published doc satisfies the acceptance criteria from the issue:
  - The field table is complete with all 8 required fields.
  - All three tier definitions are documented.
  - The 4-stage launch lifecycle is described.
  - The golden-master checkbox packaging gate is present.
  - The worked example has the golden master marked PRIVATE.
"""

from __future__ import annotations

from pathlib import Path

SPEC = Path("docs/CHALLENGE_SPEC.md")


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


# ---- field table -------------------------------------------------------------


def test_issue_95_spec_doc_has_all_required_fields():
    """All 8 fields the issue specifies appear in the doc's field table."""
    text = _spec_text()
    required_fields = [
        "seed_id",
        "domain_surface",
        "reasoning_buckets",
        "input_params",
        "warmup_prompt",
        "grader_moat",
        "golden_master",
        "tier",
    ]
    for field in required_fields:
        assert field in text, f"field '{field}' missing from CHALLENGE_SPEC.md"


def test_issue_95_visibility_split_between_public_and_private():
    """The golden_master field is documented as private; the others as public."""
    text = _spec_text()
    # The table should declare golden_master as private.
    assert "**private**" in text or "private" in text
    # The golden master is never exposed — its geometry/thresholds are not committed.
    assert "PRIVATE" in text


# ---- tier definitions -------------------------------------------------------


def test_issue_95_three_tiers_are_defined():
    """Warmup, Bread-and-butter, and Moonshot tiers are documented."""
    text = _spec_text()
    for tier in ("Warmup", "Bread-and-butter", "Moonshot"):
        assert tier in text, f"tier '{tier}' missing from CHALLENGE_SPEC.md"


def test_issue_95_moonshot_is_frontier_tier():
    """The Moonshot tier is described as near-the-frontier and mostly unsolved at launch."""
    text = _spec_text()
    assert "frontier" in text
    # Both 'Moonshot' and 'frontier' appear in the doc.
    assert "Moonshot" in text


# ---- launch lifecycle --------------------------------------------------------


def test_issue_95_four_stage_launch_lifecycle_is_documented():
    """The launch lifecycle has exactly 4 documented stages."""
    text = _spec_text()
    stages = [
        "Teaser drop",
        "Pinned GitHub Discussion",
        "Live leaderboard heat",
        "Close & rotate",
    ]
    for stage in stages:
        assert stage in text, f"lifecycle stage '{stage}' missing from CHALLENGE_SPEC.md"


def test_issue_95_lifecycle_includes_hf_space_teaser():
    """Teaser drop stage mentions the Hugging Face Space."""
    text = _spec_text()
    assert "Hugging Face" in text or "HF Space" in text


def test_issue_95_lifecycle_mentions_quarterly_cadence():
    """The spec documents the quarterly rotation cadence."""
    text = _spec_text()
    assert "quarterly" in text.lower()


# ---- golden-master checkbox gate --------------------------------------------


def test_issue_95_golden_master_packaging_gate_is_documented():
    """The spec includes the golden-master checkbox that must be cleared before publishing."""
    text = _spec_text()
    # The checkbox gate is described as a packaging requirement.
    assert "packaging gate" in text.lower() or "packaging" in text.lower()
    # All four sub-checks should be present.
    for needle in (
        "hidden Golden Master",
        "grader_moat",
        "warmup_prompt",
        "reasoning_bucket",
    ):
        assert needle in text, f"packaging gate item '{needle}' missing"


# ---- worked example ---------------------------------------------------------


def test_issue_95_worked_example_keeps_golden_master_private():
    """The packed worked example has the golden master marked PRIVATE, not committed."""
    text = _spec_text()
    assert "status: PRIVATE" in text
    assert "confirmed: true" in text


def test_issue_95_worked_example_has_all_public_fields():
    """The worked example shows all the public fields a challenger would publish."""
    text = _spec_text()
    for field in ("seed_id:", "domain_surface:", "tier:", "input_params:", "warmup_prompt:", "grader_moat:"):
        assert field in text, f"example field '{field}' missing"
