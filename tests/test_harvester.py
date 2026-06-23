"""Tests for supervisor harvest daemon + correlation report (Epic #569, Story #575)."""

import json
from pathlib import Path

import pytest

from telemetry.harvester import harvest
from telemetry.report import correlate
from telemetry.schema import SessionTelemetry
from telemetry.store import append, read_all


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_session_dir(tmp_path: Path, session_id: str = "test-session-1") -> Path:
    d = tmp_path / session_id
    d.mkdir()

    (d / "meta.json").write_text(
        json.dumps({"session_id": session_id, "agent_id": "sonnet-4-6", "duration_seconds": 3600.0}),
        encoding="utf-8",
    )
    (d / "pane.log").write_text(
        "Starting session\nDoing work\n<summary>Prior work done</summary>\nMore work\n",
        encoding="utf-8",
    )
    (d / "payload.json").write_text(
        json.dumps({
            "total_input_tokens": 50000,
            "total_output_tokens": 8000,
            "turns": [
                {"role": "user", "content": "Do the task", "token_delta": 200, "has_compaction": False, "has_tool_clearing": False},
                {"role": "assistant", "content": "Working on it", "token_delta": 500, "has_compaction": False, "has_tool_clearing": False},
                {"role": "assistant", "content": "<summary>Prior work done</summary>", "token_delta": -1000, "has_compaction": True, "has_tool_clearing": False},
                {"role": "assistant", "content": "Final output", "token_delta": 300, "has_compaction": False, "has_tool_clearing": False},
            ],
        }),
        encoding="utf-8",
    )
    (d / "git_events.json").write_text(
        json.dumps([
            {"type": "issue_closed"},
            {"type": "issue_closed"},
            {"type": "issue_closed"},
            {"type": "pr_opened"},
            {"type": "pr_opened"},
            {"type": "pr_opened"},
            {"type": "ci_failed_resolved"},
            {"type": "pr_diff_read", "pr_id": "10"},
            {"type": "pr_amended", "pr_id": "10"},
            {"type": "lines_added", "count": 300},
            {"type": "lines_overwritten", "count": 30},
        ]),
        encoding="utf-8",
    )
    (d / "tool_events.json").write_text(
        json.dumps([
            {"tool": "Bash", "turn_id": "t1", "failed": False},
            {"tool": "Read", "turn_id": "t1", "failed": False},
            {"tool": "Edit", "turn_id": "t2", "failed": False},
            {"tool": "Bash", "turn_id": "t3", "failed": False},
        ]),
        encoding="utf-8",
    )
    (d / "prompt.txt").write_text(
        "Implement telemetry stories. Verify: pytest -q tests/. If it fails, check imports.",
        encoding="utf-8",
    )
    (d / "backlog.json").write_text(
        json.dumps([
            {"title": "Story 570", "body": "Schema story", "files_modified": 3, "issues_closed_by_pr": 1},
            {"title": "Story 571", "body": "Context story", "files_modified": 2, "issues_closed_by_pr": 1},
            {"title": "Story 572", "body": "Git story", "files_modified": 2, "issues_closed_by_pr": 1},
        ]),
        encoding="utf-8",
    )
    return d


# ── Harvest tests ─────────────────────────────────────────────────────────────

def test_harvest_returns_session_telemetry(tmp_path):
    d = make_session_dir(tmp_path)
    store = str(tmp_path / "sessions.jsonl")
    result = harvest(d, store_path=store)
    assert isinstance(result, SessionTelemetry)


def test_harvest_session_id_populated(tmp_path):
    d = make_session_dir(tmp_path, session_id="sess-abc")
    result = harvest(d, store_path=str(tmp_path / "s.jsonl"))
    assert result.session_id == "sess-abc"


def test_harvest_git_metrics_populated(tmp_path):
    d = make_session_dir(tmp_path)
    result = harvest(d, store_path=str(tmp_path / "s.jsonl"))
    assert result.git_metrics["pull_requests_opened"] == 3
    assert result.git_metrics["issues_closed"] == 3


def test_harvest_appends_one_line(tmp_path):
    d = make_session_dir(tmp_path)
    store = str(tmp_path / "sessions.jsonl")
    harvest(d, store_path=store)
    records = read_all(path=store)
    assert len(records) == 1


def test_harvest_appends_additional_line(tmp_path):
    d1 = make_session_dir(tmp_path, session_id="s1")
    d2 = make_session_dir(tmp_path, session_id="s2")
    store = str(tmp_path / "sessions.jsonl")
    harvest(d1, store_path=store)
    harvest(d2, store_path=store)
    records = read_all(path=store)
    assert len(records) == 2


def test_harvest_payload_non_empty(tmp_path):
    d = make_session_dir(tmp_path)
    result = harvest(d, store_path=str(tmp_path / "s.jsonl"))
    assert result.telemetry["total_input_tokens"] > 0


# ── Correlation report tests ───────────────────────────────────────────────────

def test_report_min_sample_guard_below(tmp_path):
    store = str(tmp_path / "sessions.jsonl")
    # Add 3 sessions (< min_n=5)
    for i in range(3):
        s = SessionTelemetry(
            session_id=f"s{i}", agent_id="a", duration_seconds=100.0,
            git_metrics={"issues_closed": i, "pull_requests_opened": i, "failed_ci_runs_resolved": 0},
        )
        append(s, path=store)

    result = correlate(path=store, min_n=5)
    assert result["status"] == "insufficient_sample"
    assert result["n"] == 3


def test_report_returns_ranking_at_min_n(tmp_path):
    store = str(tmp_path / "sessions.jsonl")
    for i in range(5):
        s = SessionTelemetry(
            session_id=f"s{i}", agent_id="a", duration_seconds=float(i * 100),
            telemetry={
                "total_input_tokens": 1000 * i,
                "total_output_tokens": 200 * i,
                "compaction_events": i,
                "tool_clearing_events": 0,
                "context_velocity_avg_tokens_per_turn": float(100 - i * 10),
                "batch_ratio": float(i) / 5,
            },
            git_metrics={"issues_closed": i, "pull_requests_opened": i, "failed_ci_runs_resolved": 0},
            tool_metrics={"bash_invocations": i, "programmatic_tool_batches": i, "failed_tool_calls": 0},
        )
        append(s, path=store)

    result = correlate(path=store, min_n=5)
    assert result["status"] == "ok"
    assert result["n"] == 5
    assert "rankings" in result
    assert len(result["rankings"]) > 0


def test_report_custom_min_n(tmp_path):
    store = str(tmp_path / "sessions.jsonl")
    s = SessionTelemetry(session_id="s1", agent_id="a", duration_seconds=60.0)
    append(s, path=store)

    # With min_n=1 there should be enough data
    result = correlate(path=store, min_n=1)
    assert result["status"] == "ok"
