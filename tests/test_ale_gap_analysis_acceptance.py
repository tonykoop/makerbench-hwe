"""Issue #163 acceptance lock for the ALE-style task gap analysis.

The lower-level ALE tests validate schema and packet structure. This file keeps
the original story contract visible in one place: inventory the ALE-style
3D/engineering categories, map them against current MakerBench-HWE families,
prepare follow-up packets for the missing families, and keep all proposed
grading deterministic or tool-derived.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml", reason="PyYAML not installed (not part of the locked grading env)"
)

ROOT = Path(__file__).resolve().parents[1]
ALE_YAML = ROOT / "docs" / "ale_gap_analysis.yaml"
ALE_MD = ROOT / "docs" / "ALE_GAP_ANALYSIS.md"

REQUIRED_GAPS = {
    "scene-assembly",
    "cam-toolpath",
    "dynamic-assembly",
    "feature-tree-repair",
}

CURRENT_FAMILY_SENTINELS = {
    "vented_plate",
    "sheet_metal_bracket",
    "laser_tab_slot_panel",
    "enclosure_fastened",
    "assembly_pillow_block_shaft",
    "reverse_engineer_plate_image",
    "scan_to_brep_parametric",
    "simulation_fea",
}

FORBIDDEN_JUDGE_TOKENS = {
    "llm-judge",
    "vlm-judge",
    "llm judge",
    "vlm judge",
    "human-preference",
}


def _load() -> dict:
    data = yaml.safe_load(ALE_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "ALE gap analysis must be machine-readable"
    return data


def test_story_163_sources_are_explicit():
    source = _load()["source"]
    assert "Agents' Last Exam" in source["ale_benchmark"]
    assert "4D Matrix" in source["matrix_notes"]
    assert "Clippings/New AI Tools - AI Search YT video.md" in source["clipping"]
    assert source["reviewed_on"].isoformat() == "2026-06-14"
    assert source["registry"] == "tasks/registry.json"


def test_inventory_maps_covered_partial_and_gap_categories():
    categories = _load()["categories"]
    coverage = {cat["coverage"] for cat in categories}
    assert {"covered", "partial", "gap"}.issubset(coverage)

    mapped_families = {
        family
        for cat in categories
        for family in cat["maps_to"]
    }
    missing = CURRENT_FAMILY_SENTINELS - mapped_families
    assert not missing, f"current MakerBench families missing from ALE map: {sorted(missing)}"


def test_required_gap_categories_have_fileable_follow_up_packets():
    data = _load()
    gap_slugs = {cat["follow_up"] for cat in data["categories"] if cat["coverage"] == "gap"}
    follow_ups = {fu["slug"]: fu for fu in data["follow_ups"]}

    assert gap_slugs == REQUIRED_GAPS
    assert set(follow_ups) == REQUIRED_GAPS

    for slug, follow_up in follow_ups.items():
        packet_path = ROOT / follow_up["issue_packet"]
        assert packet_path == ROOT / "docs" / "ale_followups" / f"{slug}.md"
        assert packet_path.is_file(), f"{slug}: missing ready-to-file packet"

        packet = packet_path.read_text(encoding="utf-8")
        lowered = packet.lower()
        assert "Refs #163" in packet
        assert "Closes #" not in packet
        assert "## Grader" in packet
        assert "## Acceptance" in packet
        assert follow_up["grading"] in packet
        assert "no llm" in lowered or "no llm/vlm judge" in lowered


def test_grading_modes_stay_math_or_tool_based():
    data = _load()
    grading_values = [cat["grading"] for cat in data["categories"]]
    grading_values += [fu["grading"] for fu in data["follow_ups"]]
    lowered = " ".join(grading_values).lower()

    for token in FORBIDDEN_JUDGE_TOKENS:
        assert token not in lowered
    assert {"deterministic-geometric", "tool-execution"}.issubset(grading_values)


def test_public_analysis_renders_story_contract():
    text = ALE_MD.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "Agents' Last Exam" in text
    assert "4D matrix" in text
    assert "Coverage map" in text
    assert "Gaps" in text
    assert "math/tool-based" in lowered
    assert "never" in lowered and "llm-judged" in lowered
    for slug in REQUIRED_GAPS:
        assert f"ale_followups/{slug}.md" in text
