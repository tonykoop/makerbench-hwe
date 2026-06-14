"""Schema + parity checks for the Agents' Last Exam gap-analysis artifacts.

``docs/ale_gap_analysis.yaml`` is the machine-readable inventory of ALE-style
3D/engineering task categories mapped against MakerBench-HWE task families
(issue #163); ``docs/ALE_GAP_ANALYSIS.md`` is its public rendering. These tests
keep the inventory honest:

- the YAML parses and every category carries the core fields;
- coverage / grading values use the documented vocabulary;
- grading is *never* an LLM/VLM judge (the issue's binding constraint);
- every ``maps_to`` family actually exists (registry family id, on-disk task
  directory, or a known optional-local grader profile);
- every ``gap`` category names a follow-up that is defined in ``follow_ups``,
  and the four acceptance-sketch follow-ups are all present;
- the MD rendering and the YAML stay in sync on category titles.

PyYAML is not part of the locked grading environment (requirements.lock), so
these checks skip where it is unavailable rather than adding a runtime
dependency for a docs artifact (same policy as test_landscape_yaml.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml", reason="PyYAML not installed (not part of the locked grading env)"
)

ROOT = Path(__file__).resolve().parents[1]
ALE_YAML = ROOT / "docs" / "ale_gap_analysis.yaml"
ALE_MD = ROOT / "docs" / "ALE_GAP_ANALYSIS.md"
REGISTRY = ROOT / "tasks" / "registry.json"
TASKS_DIR = ROOT / "tasks"

ALLOWED_COVERAGE = {"covered", "partial", "gap"}

# Documented in the yaml header. Strictly math/tool-based — an LLM/VLM judge
# token must NEVER appear here (test_grading_is_never_llm_judged enforces it).
ALLOWED_GRADING = {
    "deterministic-geometric",
    "numeric-constraint",
    "vector-2d",
    "catalog-tool",
    "simulation-fea",
    "tool-execution",
}
FORBIDDEN_GRADING_TOKENS = {"llm-judge", "vlm-judge", "human-preference", "llm", "vlm"}

REQUIRED_CATEGORY_FIELDS = {
    "id",
    "title",
    "ale_axis",
    "description",
    "coverage",
    "maps_to",
    "grading",
}

# Optional-local grader profiles that are legitimate map targets without being
# registry families or task directories.
KNOWN_GRADER_PROFILES = {"simulation_fea"}

# Acceptance sketch (#163) names these four missing families explicitly.
REQUIRED_FOLLOW_UP_SLUGS = {
    "scene-assembly",
    "cam-toolpath",
    "dynamic-assembly",
    "feature-tree-repair",
}


def _load() -> dict:
    data = yaml.safe_load(ALE_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "ale_gap_analysis.yaml must parse to a mapping"
    return data


def _categories() -> list[dict]:
    cats = _load()["categories"]
    assert isinstance(cats, list) and cats, "categories must be a non-empty list"
    return cats


def _follow_ups() -> list[dict]:
    fus = _load()["follow_ups"]
    assert isinstance(fus, list) and fus, "follow_ups must be a non-empty list"
    return fus


def _known_families() -> set[str]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    fams = {tf["id"] for tf in registry["task_families"]}
    fams |= {p.name for p in TASKS_DIR.iterdir() if p.is_dir()}
    fams |= KNOWN_GRADER_PROFILES
    return fams


def test_top_level_structure():
    data = _load()
    assert "source" in data, "top-level `source` block is required"
    assert "categories" in data, "top-level `categories` list is required"
    assert "follow_ups" in data, "top-level `follow_ups` list is required"


def test_every_category_has_required_fields():
    for cat in _categories():
        missing = REQUIRED_CATEGORY_FIELDS - cat.keys()
        assert not missing, f"{cat.get('id', '<unnamed>')}: missing {sorted(missing)}"
        for field in REQUIRED_CATEGORY_FIELDS - {"maps_to"}:
            assert cat[field] not in (None, ""), (
                f"{cat['id']}: required field `{field}` is empty"
            )
        assert isinstance(cat["maps_to"], list), f"{cat['id']}: maps_to must be a list"


def test_category_ids_are_unique():
    ids = [c["id"] for c in _categories()]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate category ids: {sorted(dupes)}"


def test_coverage_uses_documented_vocabulary():
    for cat in _categories():
        assert cat["coverage"] in ALLOWED_COVERAGE, (
            f"{cat['id']}: coverage {cat['coverage']!r} not in {sorted(ALLOWED_COVERAGE)}"
        )


def test_grading_uses_documented_vocabulary():
    for cat in _categories():
        assert cat["grading"] in ALLOWED_GRADING, (
            f"{cat['id']}: grading {cat['grading']!r} not in {sorted(ALLOWED_GRADING)} "
            "(extend the yaml header vocab and this test together if genuinely new)"
        )


def test_grading_is_never_llm_judged():
    """The issue's binding constraint: keep grading math/tool-based, not LLM-judged."""
    for cat in _categories():
        assert cat["grading"] not in FORBIDDEN_GRADING_TOKENS, (
            f"{cat['id']}: grading {cat['grading']!r} is an LLM/VLM judge — #163 "
            "requires math/tool-based grading only"
        )
    for fu in _follow_ups():
        assert fu.get("grading") not in FORBIDDEN_GRADING_TOKENS, (
            f"follow-up {fu.get('slug')!r}: grading must be math/tool-based, not a judge"
        )


def test_mapped_families_exist():
    known = _known_families()
    for cat in _categories():
        for fam in cat["maps_to"]:
            assert fam in known, (
                f"{cat['id']}: maps_to family {fam!r} is not a registry family, a "
                f"tasks/ directory, or a known grader profile"
            )


def test_covered_and_partial_have_mappings_gaps_do_not():
    for cat in _categories():
        if cat["coverage"] in {"covered", "partial"}:
            assert cat["maps_to"], (
                f"{cat['id']}: coverage {cat['coverage']} but maps_to is empty"
            )
        else:  # gap
            assert not cat["maps_to"], (
                f"{cat['id']}: coverage gap but maps_to is non-empty — pick covered/partial"
            )
            assert cat.get("follow_up"), f"{cat['id']}: gap must name a follow_up slug"


def test_gap_follow_ups_are_defined():
    fu_slugs = {fu["slug"] for fu in _follow_ups()}
    for cat in _categories():
        if cat["coverage"] == "gap":
            assert cat["follow_up"] in fu_slugs, (
                f"{cat['id']}: follow_up {cat['follow_up']!r} has no follow_ups entry"
            )


def test_acceptance_follow_ups_all_present():
    fu_slugs = {fu["slug"] for fu in _follow_ups()}
    missing = REQUIRED_FOLLOW_UP_SLUGS - fu_slugs
    assert not missing, f"#163 acceptance follow-ups missing: {sorted(missing)}"
    for fu in _follow_ups():
        assert fu.get("title"), f"follow-up {fu['slug']}: title required"
        assert fu.get("summary"), f"follow-up {fu['slug']}: summary required"


def test_follow_ups_match_gap_categories():
    """No orphan follow-ups: every follow_up slug is referenced by a gap category."""
    referenced = {c["follow_up"] for c in _categories() if c["coverage"] == "gap"}
    for fu in _follow_ups():
        assert fu["slug"] in referenced, (
            f"follow-up {fu['slug']!r} is not referenced by any gap category"
        )


def test_md_renders_every_category_title():
    md = ALE_MD.read_text(encoding="utf-8")
    for cat in _categories():
        # Title may be bolded (gap rows) in the MD table; strip markdown emphasis.
        title = cat["title"]
        assert title in md, (
            f"category title {title!r} from the yaml is not rendered in "
            "ALE_GAP_ANALYSIS.md (keep the doc and data in sync)"
        )
