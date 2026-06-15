"""Ready-to-file ALE follow-up issue packets (issue #163, epic #243).

#163's acceptance is to "file follow-up issues" for the four ALE gap categories.
The sprint agent runs without GitHub-issue write access, so the deliverable is a
ready-to-file packet per gap (full body with a deterministic/tool grader and a
math/tool-based acceptance) that a maintainer can open directly.

These checks are deliberately YAML-free (unlike test_ale_gap_analysis.py) so they
run in public CI regardless of whether PyYAML is installed.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKET_DIR = ROOT / "docs" / "ale_followups"
ALE_MD = ROOT / "docs" / "ALE_GAP_ANALYSIS.md"

# The four ALE gap categories with no MakerBench family (issue #163 acceptance).
REQUIRED_SLUGS = {
    "scene-assembly": "deterministic-geometric",
    "cam-toolpath": "tool-execution",
    "dynamic-assembly": "deterministic-geometric",
    "feature-tree-repair": "tool-execution",
}

# A packet must never propose an LLM/VLM judge (the issue's binding constraint).
FORBIDDEN = ("llm-judge", "vlm-judge", "llm judge", "vlm judge", "human-preference")


def test_one_packet_per_required_gap():
    present = {p.stem for p in PACKET_DIR.glob("*.md")}
    assert set(REQUIRED_SLUGS).issubset(present), (
        f"missing follow-up packets: {sorted(set(REQUIRED_SLUGS) - present)}"
    )


def test_each_packet_is_a_fileable_issue_with_a_concrete_grader():
    for slug, grading in REQUIRED_SLUGS.items():
        text = (PACKET_DIR / f"{slug}.md").read_text(encoding="utf-8")
        lowered = text.lower()
        # Refs the study + epic (Refs, never Closes), names its grading mode and a
        # grader + acceptance section, and explicitly rules out an LLM judge.
        assert "Refs #163" in text, slug
        assert "Refs #243" in text, slug
        assert "Closes #" not in text, slug
        assert grading in text, f"{slug}: packet must name grading mode {grading!r}"
        assert "## Grader" in text, slug
        assert "## Acceptance" in text, slug
        assert "no llm" in lowered or "no llm/vlm judge" in lowered, slug
        for token in FORBIDDEN:
            assert token not in lowered or "no llm" in lowered, slug


def test_analysis_links_every_packet():
    md = ALE_MD.read_text(encoding="utf-8")
    for slug in REQUIRED_SLUGS:
        assert f"ale_followups/{slug}.md" in md, f"analysis does not link packet {slug}"
