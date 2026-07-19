"""Tests for the telemetry CLI entrypoint (Epic #569, wire #581).

Exercises ``python -m telemetry`` (telemetry.__main__:main) end-to-end: the
harvest subcommand assembles a session directory into the JSONL store, and the
report subcommand ranks signal correlations off that store.
"""

import json

import pytest

from telemetry.__main__ import main
from telemetry.store import read_all


def _write_session(session_dir, *, session_id, agent_id, prs_opened, issues_closed):
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "meta.json").write_text(
        json.dumps(
            {"session_id": session_id, "agent_id": agent_id, "duration_seconds": 90.0}
        ),
        encoding="utf-8",
    )
    git_events = [{"type": "pr_opened"} for _ in range(prs_opened)]
    git_events += [{"type": "issue_closed"} for _ in range(issues_closed)]
    (session_dir / "git_events.json").write_text(json.dumps(git_events), encoding="utf-8")
    (session_dir / "tool_events.json").write_text(
        json.dumps([{"tool": "Bash", "turn_id": "t1"}, {"tool": "Read", "turn_id": "t1"}]),
        encoding="utf-8",
    )
    (session_dir / "prompt.txt").write_text("Implement the feature. Verify: pytest.", encoding="utf-8")
    (session_dir / "backlog.json").write_text(
        json.dumps([{"title": "task", "files_modified": 2, "issues_closed_by_pr": 1}]),
        encoding="utf-8",
    )
    return session_dir


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "makerbench-telemetry" in capsys.readouterr().out


def test_harvest_writes_store(tmp_path):
    session = _write_session(
        tmp_path / "sess-1", session_id="sess-1", agent_id="agent-a",
        prs_opened=3, issues_closed=2,
    )
    store = tmp_path / "sessions.jsonl"

    rc = main(["harvest", str(session), "--store", str(store)])
    assert rc == 0

    records = read_all(path=str(store))
    assert len(records) == 1
    assert records[0].session_id == "sess-1"
    assert records[0].git_metrics["pull_requests_opened"] == 3


def test_report_insufficient_sample(tmp_path, capsys):
    session = _write_session(
        tmp_path / "sess-1", session_id="sess-1", agent_id="agent-a",
        prs_opened=1, issues_closed=1,
    )
    store = tmp_path / "sessions.jsonl"
    main(["harvest", str(session), "--store", str(store)])
    capsys.readouterr()  # flush the harvest command's stdout

    rc = main(["report", "--store", str(store), "--min-n", "5"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "insufficient_sample"
    assert out["n"] == 1


def test_harvest_then_report_ranks_signals(tmp_path):
    store = tmp_path / "sessions.jsonl"
    for i in range(3):
        session = _write_session(
            tmp_path / f"sess-{i}", session_id=f"sess-{i}", agent_id="agent-a",
            prs_opened=i, issues_closed=i,
        )
        assert main(["harvest", str(session), "--store", str(store)]) == 0

    report_path = tmp_path / "report.json"
    rc = main(["report", "--store", str(store), "--min-n", "3", "-o", str(report_path)])
    assert rc == 0

    result = json.loads(report_path.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert result["n"] == 3
    assert result["outcome"] == "pull_requests_opened"
    assert isinstance(result["rankings"], list) and result["rankings"]
