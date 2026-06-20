"""Coverage inventory + blind-vs-perception gap report tests (mb#51)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.coverage_report import (
    adapter_expected_id,
    adapter_inventory,
    build_report,
    core_family_ids,
    format_markdown,
    gap_rows,
    is_infra_error,
    iter_result_bundles,
    model_key,
    perception_backlog,
    registry_version,
    scan,
    track_overall,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE = ["vented_plate", "enclosure_fastened"]


def _grade(score, *, infra=False):
    if infra:
        return {
            "score": None,
            "levels": [{"level": 1, "passed": False, "checks": {"agent_ok": False}}],
        }
    return {"score": score, "levels": [{"level": 1, "passed": True, "checks": {"agent_ok": True}}]}


def _bundle(agent_id, model, rl, rows, *, version="0.1.0"):
    return {
        "benchmark_version": version,
        "agent_identifier": agent_id,
        "model_identifier": model,
        "reasoning_level": rl,
        "results": rows,
    }


def _row(task_id, track, score, *, infra=False):
    return {"task_id": task_id, "track": track, "grade": _grade(score, infra=infra)}


# --- small helpers ---------------------------------------------------------


def test_adapter_expected_id_aliases_and_cli():
    assert adapter_expected_id("anthropic_agent.py") == "anthropic_api"
    assert adapter_expected_id("qwen_agent.py") == "qwen_api"
    assert adapter_expected_id("claude_cli_agent.py") == "claude_cli"
    assert adapter_expected_id("openrouter_agent.py") == "openrouter"


def test_is_infra_error_detects_agent_failures():
    assert is_infra_error(_grade(None, infra=True))
    assert is_infra_error({"notes": "agent_error", "levels": []})
    assert not is_infra_error(_grade(3))


def test_model_key_matches_leaderboard_convention():
    assert model_key("codex-gpt-5.5", "high") == "codex-gpt-5.5 [high]"
    assert model_key("baseline-v0", None) == "baseline-v0"


# --- scan + means ----------------------------------------------------------


def test_scan_groups_by_model_track_family_and_skips_infra():
    bundles = [
        _bundle("anthropic_api", "claude-x", "high", [
            _row("vented_plate", "blind", 4),
            _row("vented_plate", "blind", 2),       # second seed, same family
            _row("enclosure_fastened", "blind", 3),
            _row("vented_plate", "perception", 1),
            _row("enclosure_fastened", "blind", 0, infra=True),  # excluded
        ]),
    ]
    scanned = scan(bundles, CORE)
    cells = scanned["cells"]
    assert cells[("claude-x [high]", "blind")]["vented_plate"] == [4.0, 2.0]
    assert cells[("claude-x [high]", "blind")]["enclosure_fastened"] == [3.0]
    assert "anthropic_api" in scanned["present_agent_ids"]


def test_track_overall_is_mean_of_family_means():
    # vented_plate mean = (4+2)/2 = 3; enclosure mean = 3 -> overall = 3.0
    bundles = [
        _bundle("anthropic_api", "claude-x", "high", [
            _row("vented_plate", "blind", 4),
            _row("vented_plate", "blind", 2),
            _row("enclosure_fastened", "blind", 3),
        ]),
    ]
    cells = scan(bundles, CORE)["cells"]
    assert track_overall(cells, "claude-x [high]", "blind") == 3.0
    assert track_overall(cells, "claude-x [high]", "perception") is None


def test_gap_rows_compute_perception_minus_blind():
    bundles = [
        _bundle("anthropic_api", "m1", None, [
            _row("vented_plate", "blind", 2),
            _row("vented_plate", "perception", 3),
        ]),
        _bundle("openrouter_api", "m2", None, [
            _row("vented_plate", "blind", 4),  # no perception -> gap None
        ]),
    ]
    cells = scan(bundles, CORE)["cells"]
    rows = {r["model"]: r for r in gap_rows(cells)}
    assert rows["m1"]["gap"] == 1.0
    assert rows["m2"]["gap"] is None
    # Sorted strongest-blind first.
    assert gap_rows(cells)[0]["model"] == "m2"


# --- perception backlog ----------------------------------------------------


def test_perception_backlog_lists_blind_only_models_cheapest_first():
    bundles = [
        # blind-only, 2 families run -> top of backlog (least work left)
        _bundle("a", "blind-rich", None, [
            _row("vented_plate", "blind", 3),
            _row("enclosure_fastened", "blind", 2),
        ]),
        # blind-only, 1 family run -> below blind-rich
        _bundle("a", "blind-thin", None, [
            _row("vented_plate", "blind", 1),
        ]),
        # ran both tracks -> excluded from backlog
        _bundle("a", "complete", None, [
            _row("vented_plate", "blind", 2),
            _row("vented_plate", "perception", 3),
        ]),
        # perception-only -> not a blind backlog item
        _bundle("a", "perc-only", None, [
            _row("vented_plate", "perception", 4),
        ]),
    ]
    cells = scan(bundles, CORE)["cells"]
    backlog = perception_backlog(gap_rows(cells))
    assert [r["model"] for r in backlog] == ["blind-rich", "blind-thin"]
    assert backlog[0]["families_blind"] == 2


def test_perception_backlog_empty_when_all_have_both_tracks():
    bundles = [
        _bundle("a", "m", None, [
            _row("vented_plate", "blind", 2),
            _row("vented_plate", "perception", 3),
        ]),
    ]
    cells = scan(bundles, CORE)["cells"]
    assert perception_backlog(gap_rows(cells)) == []


def test_format_markdown_includes_backlog_section():
    bundles = [
        _bundle("a", "blind-only", None, [_row("vented_plate", "blind", 2)]),
    ]
    gaps = gap_rows(scan(bundles, CORE)["cells"])
    md = format_markdown([], gaps, current_version="0.1.0")
    assert "Perception backlog" in md
    assert "blind-only" in md
    # The empty-state line shows when nothing is outstanding.
    full = gap_rows(scan([
        _bundle("a", "m", None, [
            _row("vented_plate", "blind", 2),
            _row("vented_plate", "perception", 3),
        ]),
    ], CORE)["cells"])
    assert "every blind-scored model" in format_markdown([], full, current_version="0.1.0")


# --- inventory -------------------------------------------------------------


def test_adapter_inventory_marks_covered_missing(tmp_path):
    adapters = tmp_path / "agents"
    adapters.mkdir()
    for name in ("anthropic_agent.py", "qwen_agent.py", "claude_cli_agent.py"):
        (adapters / name).write_text("# stub\n")
    bundles = [
        _bundle("anthropic_api", "claude-x", "high", [_row("vented_plate", "blind", 3)]),
    ]
    scanned = scan(bundles, CORE)
    inv = {r["adapter"]: r for r in adapter_inventory(scanned, adapters, current_version="0.1.0")}
    assert inv["anthropic_agent.py"]["covered"] is True
    assert inv["anthropic_agent.py"]["n_models"] == 1
    assert inv["qwen_agent.py"]["covered"] is False
    assert inv["claude_cli_agent.py"]["covered"] is False


def test_inventory_matches_api_suffix_variant(tmp_path):
    # openrouter adapter expects "openrouter"; bundles carry "openrouter_api".
    adapters = tmp_path / "agents"
    adapters.mkdir()
    (adapters / "openrouter_agent.py").write_text("# stub\n")
    bundles = [_bundle("openrouter_api", "kimi", None, [_row("vented_plate", "blind", 2)])]
    scanned = scan(bundles, CORE)
    inv = adapter_inventory(scanned, adapters, current_version="0.1.0")[0]
    assert inv["covered"] is True
    assert inv["matched_agent_ids"] == ["openrouter_api"]


def test_inventory_flags_stale_version(tmp_path):
    adapters = tmp_path / "agents"
    adapters.mkdir()
    (adapters / "anthropic_agent.py").write_text("# stub\n")
    bundles = [
        _bundle("anthropic_api", "old", None, [_row("vented_plate", "blind", 2)], version="0.0.9"),
    ]
    scanned = scan(bundles, CORE)
    inv = adapter_inventory(scanned, adapters, current_version="0.1.0")[0]
    assert inv["covered"] is True
    assert inv["stale"] is True
    assert inv["benchmark_versions"] == ["0.0.9"]


# --- end to end ------------------------------------------------------------


def test_format_markdown_has_both_sections():
    bundles = [
        _bundle("anthropic_api", "m1", None, [
            _row("vented_plate", "blind", 2),
            _row("vented_plate", "perception", 3),
        ]),
    ]
    cells = scan(bundles, CORE)["cells"]
    md = format_markdown([], gap_rows(cells), current_version="0.1.0")
    assert "Adapter inventory" in md
    assert "Blind-vs-perception gap" in md
    assert "m1" in md


def test_build_report_on_committed_results():
    """Smoke test against the real repo state — must produce both sections."""
    report = build_report()
    assert report["benchmark_version"] is not None
    assert report["n_core_families"] >= 1
    # At least the CLI adapters that have committed bundles show as covered.
    covered = {r["adapter"] for r in report["adapter_inventory"] if r["covered"]}
    assert "claude_cli_agent.py" in covered
    assert "codex_cli_agent.py" in covered
    # The gap report has rows, and at least one model ran both tracks.
    both = [r for r in report["blind_vs_perception"] if r["gap"] is not None]
    assert both, "expected at least one model with blind+perception coverage"
    # Backlog is the blind-only subset: present in the payload and never overlaps
    # the both-tracks set.
    backlog_models = {r["model"] for r in report["perception_backlog"]}
    assert backlog_models.isdisjoint({r["model"] for r in both})


def test_committed_coverage_reports_match_generator():
    """The checked-in #51 report artifacts must not drift from the generator."""
    report = build_report()
    expected_md = format_markdown(
        report["adapter_inventory"],
        report["blind_vs_perception"],
        current_version=report["benchmark_version"],
        backlog=report["perception_backlog"],
    ) + "\n"
    expected_json = json.dumps(report, indent=2, sort_keys=True) + "\n"

    assert (REPO_ROOT / "docs" / "COVERAGE_REPORT.md").read_text(encoding="utf-8") == expected_md
    assert (REPO_ROOT / "docs" / "coverage_report.json").read_text(encoding="utf-8") == expected_json


def test_core_family_ids_and_version_load_from_registry():
    ids = core_family_ids(REPO_ROOT / "tasks" / "registry.json")
    assert "vented_plate" in ids
    assert registry_version(REPO_ROOT / "tasks" / "registry.json")


def test_iter_result_bundles_skips_non_bundles(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps(_bundle("a", "m", None, [])))
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "other.json").write_text(json.dumps({"no": "results"}))
    found = list(iter_result_bundles(tmp_path))
    assert len(found) == 1
    assert found[0]["model_identifier"] == "m"
