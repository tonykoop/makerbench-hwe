"""Contract tests for the `makerbench video-check` CLI (#105).

The video_evidence schema and disclosure-grade protocol hooks land elsewhere;
this exercises the runnable entry point so a contributor can validate their
session recording without writing Python.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from makerbench.cli import app

runner = CliRunner()

# The committed example manifest embeds a valid video_evidence block.
MANIFEST_WITH_VIDEO = "schemas/examples/workflow_manifest.example.json"
# The closed-loop demo manifest has no video_evidence.
MANIFEST_WITHOUT_VIDEO = "examples/closed_loop_instrument_demo.workflow_manifest.json"


# ---------------------------------------------------------------------------
# happy-path: manifest with a valid protocol-compliant recording
# ---------------------------------------------------------------------------


def test_valid_recording_reports_complete():
    result = runner.invoke(app, ["video-check", MANIFEST_WITH_VIDEO])

    assert result.exit_code == 0
    assert "video_evidence" in result.stdout
    assert "valid" in result.stdout


def test_valid_recording_surfaces_all_checks():
    result = runner.invoke(app, ["video-check", MANIFEST_WITH_VIDEO])

    assert result.exit_code == 0
    for check in (
        "video_present",
        "hosted_url_present",
        "capture_mode_declared",
        "integrity_hash_present",
        "duration_declared",
        "three_phases_in_order",
        "segments_contiguous",
        "prompt_init_window",
        "verdict_reaches_end",
    ):
        assert check in result.stdout


def test_valid_recording_json_output_is_structured_and_passing():
    result = runner.invoke(app, ["video-check", "--json", MANIFEST_WITH_VIDEO])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["category"] == "video_evidence"
    assert payload["passed"] is True
    assert payload["checks"]["three_phases_in_order"] is True
    assert payload["checks"]["segments_contiguous"] is True


def test_strict_stays_zero_on_valid_recording():
    result = runner.invoke(app, ["video-check", "--strict", MANIFEST_WITH_VIDEO])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# absent recording: not-applicable, not a hard failure by default
# ---------------------------------------------------------------------------


def test_absent_recording_exits_zero_by_default():
    result = runner.invoke(app, ["video-check", MANIFEST_WITHOUT_VIDEO])

    assert result.exit_code == 0
    assert "absent" in result.stdout


def test_absent_recording_json_shows_video_present_false():
    result = runner.invoke(app, ["video-check", "--json", MANIFEST_WITHOUT_VIDEO])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["checks"]["video_present"] is False


def test_strict_sets_nonzero_exit_on_absent_recording():
    result = runner.invoke(app, ["video-check", "--strict", MANIFEST_WITHOUT_VIDEO])

    assert result.exit_code == 1
