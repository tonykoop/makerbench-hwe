"""makerbench-logger SDK tests."""

from __future__ import annotations

import json

import pytest

from makerbench_logger import RunLogger, build_manifest, manifest_to_dict


def _cli_runner():
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    return CliRunner()


def test_build_manifest_computes_hii_and_redacts_secret_params():
    manifest = build_manifest(
        [
            {
                "timestamp": "2026-06-13T10:00:00Z",
                "tool": "parts_search",
                "params": {"thread": "M3", "api_key": "sk-secret"},
            },
            {
                "timestamp": "2026-06-13T10:01:00Z",
                "tool": "render",
                "args": {"view": "iso"},
                "human_intervention_level": "L2",
                "human_steering": "human selected camera",
            },
        ],
        run_id="fake-run",
        stack={
            "orchestrator": "codex",
            "framework": "makerbench",
            "host_application": "Blender",
            "execution_bridge": "mcp",
        },
        wall_clock_seconds=61.0,
        tokens={"total": 1234},
    )
    payload = manifest_to_dict(manifest)

    assert payload["metrics"] == {
        "wall_clock": 61.0,
        "wall_clock_seconds": 61.0,
        "tokens": {"total": 1234},
        "tool_calls": 2,
    }
    assert payload["human_intervention_index"]["levels"] == {"L0": 1, "L1": 0, "L2": 1}
    assert payload["human_intervention_index"]["weighted_score"] == 0.5
    assert payload["autonomy_ratio"] == 0.5
    assert payload["tool_calls"][0]["params"] == {"thread": "M3"}


def test_logger_cli_emits_valid_manifest_from_json_log(tmp_path):
    runner = _cli_runner()
    from makerbench_logger.cli import app as logger_app

    log = tmp_path / "tool_calls.json"
    out = tmp_path / "workflow_manifest.json"
    log.write_text(
        json.dumps({
            "tool_calls": [
                {"tool": "parts_search", "params": {"thread": "M3"}},
                {"tool": "render", "params": {"view": "top"}, "human_steering": True},
            ]
        }),
        encoding="utf-8",
    )

    result = runner.invoke(logger_app, [
        "emit",
        "--log",
        str(log),
        "--run-id",
        "cli-run",
        "--out",
        str(out),
        "--orchestrator",
        "codex",
        "--host-application",
        "Blender",
        "--metadata",
        "task=fixture",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["run_id"] == "cli-run"
    assert payload["stack"]["host_application"] == "Blender"
    assert payload["metadata"] == {"task": "fixture"}
    assert payload["human_intervention_index"]["levels"] == {"L0": 1, "L1": 1, "L2": 0}


def test_makerbench_cli_mounts_logger_emit(tmp_path):
    runner = _cli_runner()
    from makerbench.cli import app as makerbench_app

    log = tmp_path / "events.jsonl"
    out = tmp_path / "manifest.json"
    log.write_text(
        "\n".join([
            json.dumps({"tool": "parts_search", "params": {"thread": "M3"}}),
            json.dumps({"tool": "render", "params": {"view": "iso"}, "hii_level": "L1"}),
        ]),
        encoding="utf-8",
    )

    result = runner.invoke(makerbench_app, [
        "logger",
        "emit",
        "--log",
        str(log),
        "--run-id",
        "mounted-run",
        "--out",
        str(out),
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))["metrics"]["tool_calls"] == 2


def test_run_logger_wrap_tool_and_emit(tmp_path):
    logger = RunLogger("wrapped-run", stack={"orchestrator": "pytest"})

    def fake_tool(*, value: int) -> dict:
        return {"value": value}

    wrapped = logger.wrap_tool("fake_tool", fake_tool)
    assert wrapped(value=7) == {"value": 7}
    out = tmp_path / "wrapped.json"
    logger.emit(out, tokens={"input": 10, "output": 5, "total": 15})

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["run_id"] == "wrapped-run"
    assert payload["metrics"]["tokens"]["total"] == 15
    assert payload["tool_calls"][0]["tool"] == "fake_tool"
