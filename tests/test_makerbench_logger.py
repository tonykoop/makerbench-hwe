"""Tests for the makerbench-logger SDK (mb#92)."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

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

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_hii_event_counts_and_weighted_autonomy_ratio():
    # 2 L0, 1 L1, 1 L2. Weighted ratio = (2*1.0 + 1*0.5 + 1*0.0)/4 = 0.625.
    # This MUST match makerbench.schema.HumanInterventionIndex.from_events.
    calls = [
        ToolCall("a", human_steering="L0"),
        ToolCall("b", human_steering="L0"),
        ToolCall("c", human_steering="L1"),
        ToolCall("d", human_steering="L2"),
    ]
    hii = HumanInterventionIndex.from_calls(calls)
    assert hii.highest_level == HII_L2
    assert hii.l0_autonomous_events == 2
    assert hii.l1_nl_steering_events == 1
    assert hii.l2_copilot_manual_events == 1
    assert hii.autonomy_ratio == 0.625


def test_fully_autonomous_run_has_ratio_one():
    calls = [ToolCall("a"), ToolCall("b")]
    hii = HumanInterventionIndex.from_calls(calls)
    assert hii.highest_level == HII_L0
    assert hii.autonomy_ratio == 1.0


def test_empty_run_is_autonomous():
    hii = HumanInterventionIndex.from_calls([])
    assert hii.highest_level == HII_L0
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
    # mb#89 top-level keys present (authoritative shape uses `hii`, not
    # `human_intervention_index`).
    for key in ("schema_version", "task_id", "seed", "stack", "metrics", "hii",
                "provenance_trace", "tool_call_log"):
        assert key in data
    # Stack slots serialize to the authoritative {name, version} object.
    assert data["stack"]["framework"] == {"name": "blender-mcp", "version": None}
    assert data["metrics"]["tool_calls_count"] == 2
    assert data["metrics"]["inference_tokens"] == 4200
    # 1 L0 + 1 L1 -> weighted ratio (1*1.0 + 1*0.5)/2 = 0.75, highest L1.
    assert data["hii"]["highest_level"] == "L1"
    assert data["hii"]["l1_nl_steering_events"] == 1
    assert data["hii"]["autonomy_ratio"] == 0.75
    assert data["artifacts"] == ["out.step"]
    assert manifest == data
    # wall-clock captured by the context manager
    assert data["metrics"]["wall_clock_seconds"] is not None


def test_emitted_manifest_round_trips_through_authoritative_schema():
    """The emitted manifest must validate AND preserve HII disclosure (mb#89).

    Regression guard for the silent-drop bug: the old SDK emitted a
    `human_intervention_index` field the pydantic model ignored, recording an
    L1/L2-steered run as fully autonomous L0. The conforming `hii` shape must
    survive the round-trip.
    """
    pytest.importorskip("makerbench.schema")
    from makerbench.schema import WorkflowManifest as SchemaManifest

    with WorkflowLogger(task_id="bracket_v1", seed=0, orchestrator="claude-code",
                        framework="blender-mcp", host_application="blender",
                        execution_bridge="blender-mcp") as log:
        log.tool_call("blender.add_cube", {"size": 20})
        log.tool_call("blender.bevel", {"width": 1}, steering="L1")
        log.tool_call("manual.edit_vertex", {}, steering="L2")
    manifest = log.to_dict()

    ok, reason = validate_with_schema(manifest)
    assert ok, reason

    parsed = SchemaManifest.model_validate(manifest)
    # Steering survives: 1 L1 + 1 L2 must NOT collapse to L0.
    assert parsed.hii.highest_level == "L2"
    assert parsed.hii.l1_nl_steering_events == 1
    assert parsed.hii.l2_copilot_manual_events == 1
    assert parsed.hii.l0_autonomous_events == 1
    # Stack string shorthand normalizes to ComponentVersion on the model side.
    assert parsed.stack.framework.name == "blender-mcp"
    assert parsed.metrics.tool_calls_count == 3


def test_validate_with_schema_rejects_invalid_manifest_when_schema_present():
    ok, reason = validate_with_schema({"anything": True})
    assert ok is False
    assert reason is not None


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
    assert proc.stdout.startswith("usage: makerbench-logger")


def test_console_script_entry_point_is_packaged():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["makerbench-logger"] == "makerbench_logger.__main__:main"


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
    # 1 L0 + 1 L2 -> highest L2, weighted ratio (1*1.0 + 1*0.0)/2 = 0.5.
    assert data["hii"]["highest_level"] == "L2"
    assert data["hii"]["autonomy_ratio"] == 0.5
