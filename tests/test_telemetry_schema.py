"""Tests for SessionTelemetry schema + JSONL store (Epic #569, Story #570).

Note: named test_telemetry_schema.py rather than test_schema.py to avoid
collision with the pre-existing tests/test_schema.py (makerbench.schema).
"""

import pytest
from pydantic import ValidationError

from telemetry.schema import SessionTelemetry
from telemetry.store import append, read_all


def test_validation_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        SessionTelemetry(agent_id="a1", duration_seconds=60.0)  # missing session_id


def test_validation_rejects_missing_agent_id():
    with pytest.raises(ValidationError):
        SessionTelemetry(session_id="s1", duration_seconds=60.0)


def test_validation_rejects_missing_duration():
    with pytest.raises(ValidationError):
        SessionTelemetry(session_id="s1", agent_id="a1")


def test_defaults_populated():
    s = SessionTelemetry(session_id="s1", agent_id="a1", duration_seconds=10.0)
    assert "total_input_tokens" in s.telemetry
    assert "issues_closed" in s.git_metrics
    assert "bash_invocations" in s.tool_metrics


def test_append_read_round_trips(tmp_path):
    store = str(tmp_path / "sessions.jsonl")

    s1 = SessionTelemetry(
        session_id="sess-1",
        agent_id="agent-a",
        duration_seconds=120.0,
        git_metrics={"issues_closed": 3, "pull_requests_opened": 3, "failed_ci_runs_resolved": 0},
    )
    s2 = SessionTelemetry(
        session_id="sess-2",
        agent_id="agent-b",
        duration_seconds=240.0,
        git_metrics={"issues_closed": 7, "pull_requests_opened": 5, "failed_ci_runs_resolved": 1},
    )

    append(s1, path=store)
    append(s2, path=store)

    records = read_all(path=store)
    assert len(records) == 2
    assert records[0].session_id == "sess-1"
    assert records[0].git_metrics["issues_closed"] == 3
    assert records[1].session_id == "sess-2"
    assert records[1].git_metrics["pull_requests_opened"] == 5


def test_append_creates_parent_dir(tmp_path):
    store = str(tmp_path / "nested" / "deep" / "sessions.jsonl")
    s = SessionTelemetry(session_id="s1", agent_id="a1", duration_seconds=1.0)
    append(s, path=store)
    records = read_all(path=store)
    assert len(records) == 1


def test_read_all_empty_file(tmp_path):
    store = str(tmp_path / "empty.jsonl")
    assert read_all(path=store) == []
