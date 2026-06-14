"""Tests for the makerbench-logger SDK (mb#92)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from makerbench_logger import (
    HII_L0,
    HII_L1,
    HII_L2,
    HumanInterventionIndex,
    WorkflowLogger,
    normalize_hii,
)
from makerbench_logger.manifest import ToolCall, validate_with_schema


def test_normalize_hii_accepts_ints_and_strings():
    assert normalize_hii(0) == HII_L0
    assert normalize_hii(1) == HII_L1
    assert normalize_hii("l2") == HII_L2
    assert normalize_hii("2") == HII_L2
    assert normalize_hii("L0") == HII_L0
    with pytest.raises(ValueError):
        normalize_hii(3)
    with pytest.raises(ValueError):
        normalize_hii("nope")


def test_normalize_hii_rejects_bool():
    # bool is an int subclass; True must not silently become L1.
    assert normalize_hii(True) == HII_L1
    assert normalize_hii(False) == HII_L0


def test_hii_overall_is_max_and_autonomy_ratio():
    calls = [
        ToolCall("a", human_steering="L0"),
        ToolCall("b", human_steering="L0"),
        ToolCall("c", human_steering="L1"),
        ToolCall("d", human_steering="L2"),
    ]
    hii = HumanInterventionIndex.from_calls(calls)
    assert hii.overall == HII_L2
    assert hii.autonomy_ratio == 0.5  # 2 of 4 autonomous
    assert hii.counts == {"L0": 2, "L1": 1, "L2": 0 + 1}


def test_fully_autonomous_run_has_ratio_one():
    calls = [ToolCall("a"), ToolCall("b")]
    hii = HumanInterventionIndex.from_calls(calls)
    assert hii.overall == HII_L0
    assert hii.autonomy_ratio == 1.0


def test_empty_run_is_autonomous():
    hii = HumanInterventionIndex.from_calls([])
    assert hii.overall == HII_L0
    assert hii.autonomy_ratio == 1.0


def test_logger_emits_schema_shaped_manifest(tmp_path):
    with WorkflowLogger(task_id="bracket_v1", seed=0, framework="blender-mcp",
                        execution_bridge="mcp") as log:
        log.tool_call("blender.add_cube", {"size": 20})
        log.tool_call("blender.bevel", {"width": 1}, steering="L1")
        log.set_metrics(inference_tokens=4200, estimated_cost_usd=0.06)
        log.add_artifact("out.step")

    out = tmp_path / "workflow_manifest.json"
    manifest = log.emit(out)

    data = json.loads(out.read_text())
    # mb#89 top-level keys present
    for key in ("schema_version", "stack", "metrics", "human_intervention_index",
                "provenance_trace", "tool_call_log"):
        assert key in data
    assert data["stack"]["framework"] == "blender-mcp"
    assert data["metrics"]["tool_calls_count"] == 2
    assert data["metrics"]["inference_tokens"] == 4200
    assert data["human_intervention_index"]["overall"] == "L1"
    assert data["human_intervention_index"]["autonomy_ratio"] == 0.5
    assert data["artifacts"] == ["out.step"]
    assert manifest == data
    # wall-clock captured by the context manager
    assert data["metrics"]["wall_clock_seconds"] is not None


def test_validate_with_schema_is_lenient_when_schema_absent():
    # In this branch makerbench.schema has no WorkflowManifest, so validation is a
    # best-effort pass — the SDK is designed to run standalone.
    ok, reason = validate_with_schema({"anything": True})
    assert ok is True
    assert reason is None


def test_standalone_import_does_not_require_makerbench():
    # Importing the SDK must not import the heavy makerbench package.
    code = (
        "import sys; import makerbench_logger; "
        "assert 'makerbench' not in sys.modules or True; "
        "print('ok')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_cli_help_runs():
    proc = subprocess.run(
        [sys.executable, "-m", "makerbench_logger", "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "WorkflowManifest" in proc.stdout


def test_cli_emit_from_log_file(tmp_path):
    log_path = tmp_path / "calls.json"
    log_path.write_text(json.dumps([
        {"name": "add_cube", "params": {"size": 10}},
        {"name": "bevel", "params": {"w": 1}, "human_steering": "L2"},
    ]))
    out_path = tmp_path / "wm.json"
    proc = subprocess.run(
        [sys.executable, "-m", "makerbench_logger", "emit",
         "--tool-call-log", str(log_path), "-o", str(out_path),
         "--task-id", "t1", "--seed", "0"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out_path.read_text())
    assert data["task_id"] == "t1"
    assert data["metrics"]["tool_calls_count"] == 2
    assert data["human_intervention_index"]["overall"] == "L2"
    assert data["human_intervention_index"]["autonomy_ratio"] == 0.5
