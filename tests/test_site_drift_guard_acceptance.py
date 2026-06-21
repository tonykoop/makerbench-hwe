"""Acceptance-lock tests for committed site drift guard (#224)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "makerbench_site_check_data_drift_acceptance",
    ROOT / "site" / "check_data_drift.py",
)
check_data_drift = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_data_drift
SPEC.loader.exec_module(check_data_drift)


def test_story_224_guard_covers_data_and_generated_html_trees():
    assert "models" in check_data_drift.GENERATED_DIRS
    assert "tasks" in check_data_drift.GENERATED_DIRS
    assert check_data_drift.GENERATED_PAGE_TREES == ("models", "tasks")
    assert "leaderboard.json" in check_data_drift.GENERATED_TOP_LEVEL

    clean = check_data_drift.DriftReport()
    assert clean.has_drift is False
    assert "site/data and site/{models,tasks}" in clean.format()


def test_story_224_ci_runs_drift_guard_before_unit_tests():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    drift_idx = workflow.index("python site/check_data_drift.py")
    pytest_idx = workflow.index("xvfb-run -a pytest -q")

    assert drift_idx < pytest_idx
    assert "Audit public benchmark artifacts" in workflow
    assert "Check generated site data" in workflow
