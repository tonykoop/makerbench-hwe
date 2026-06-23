"""Tests for git throughput metrics (Epic #569, Story #572)."""

from telemetry.git_throughput import compute

FIXTURE_LOG = [
    {"type": "issue_closed"},
    {"type": "issue_closed"},
    {"type": "issue_closed"},
    {"type": "pr_opened"},
    {"type": "pr_opened"},
    {"type": "ci_failed_resolved"},
    # Self-correction sequence: read diff then amend same PR
    {"type": "pr_diff_read", "pr_id": "42"},
    {"type": "pr_amended", "pr_id": "42"},
    # Another PR read without amend — should NOT count as self-correction
    {"type": "pr_diff_read", "pr_id": "43"},
    # Code volume events
    {"type": "lines_added", "count": 200},
    {"type": "lines_overwritten", "count": 50},
]


def test_issue_to_pr_ratio():
    result = compute(FIXTURE_LOG)
    assert result["issues_closed"] == 3
    assert result["pull_requests_opened"] == 2
    assert result["issue_to_pr_ratio"] == pytest_approx(1.5)


def test_self_correction_detected():
    result = compute(FIXTURE_LOG)
    assert result["self_correction_cycles"] >= 1


def test_code_retention_ratio_in_range():
    result = compute(FIXTURE_LOG)
    r = result["code_retention_ratio"]
    assert 0.0 <= r <= 1.0


def test_code_retention_ratio_value():
    result = compute(FIXTURE_LOG)
    # 200 added, 50 overwritten: retained=150, total=250 → 0.6
    assert abs(result["code_retention_ratio"] - 0.6) < 1e-9


def test_failed_ci_runs_resolved():
    result = compute(FIXTURE_LOG)
    assert result["failed_ci_runs_resolved"] == 1


def test_issue_to_pr_ratio_zero_prs():
    log = [{"type": "issue_closed"}, {"type": "issue_closed"}]
    result = compute(log)
    assert result["issue_to_pr_ratio"] == 0.0
    assert result["pull_requests_opened"] == 0


def test_empty_log():
    result = compute([])
    assert result["issues_closed"] == 0
    assert result["pull_requests_opened"] == 0
    assert result["issue_to_pr_ratio"] == 0.0
    assert result["self_correction_cycles"] == 0
    assert result["code_retention_ratio"] == 1.0
    assert result["failed_ci_runs_resolved"] == 0


# pytest_approx shim — use the real one
try:
    from pytest import approx as pytest_approx
except ImportError:
    def pytest_approx(v, rel=1e-6):
        return v
