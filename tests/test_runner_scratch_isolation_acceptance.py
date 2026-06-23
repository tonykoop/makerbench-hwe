"""Acceptance locks for model-scoped grading scratch isolation (#220)."""

from __future__ import annotations

from typer.testing import CliRunner

from makerbench import cli
from makerbench.schema import FailureLevel, GradeResult, LevelResult, TaskResult


def test_cli_run_passes_model_identifier_to_runner(monkeypatch, tmp_path):
    seen: list[dict] = []
    written = {}

    def fake_run_one(family, seed, track, agent, **kwargs):
        seen.append(
            {
                "family": family,
                "seed": seed,
                "track": track,
                "model_identifier": kwargs.get("model_identifier"),
            }
        )
        return TaskResult(
            task_id=family,
            seed=seed,
            track=track,
            grade=GradeResult(
                task_id=family,
                track=track,
                score=1,
                levels=[LevelResult(level=FailureLevel.STRUCTURAL, passed=True)],
            ),
        )

    monkeypatch.setattr(cli, "_load_agent", lambda path: object())
    monkeypatch.setattr(cli, "run_one", fake_run_one)
    monkeypatch.setattr(cli, "_print_grade", lambda result: None)
    monkeypatch.setattr(cli, "grader_environment", lambda: {})
    monkeypatch.setattr(cli, "write_results", lambda payload, out: written.setdefault("payload", payload))

    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--task",
            "vented_plate",
            "--agent",
            "agents/demo_agent.py",
            "--track",
            "blind",
            "--seeds",
            "0",
            "--model-id",
            "model/a:b",
            "--out",
            str(tmp_path / "results.json"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == [
        {
            "family": "vented_plate",
            "seed": 0,
            "track": "blind",
            "model_identifier": "model/a:b",
        }
    ]
    assert written["payload"].model_identifier == "model/a:b"
