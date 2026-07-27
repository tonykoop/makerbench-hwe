"""Video / screen-recording submission contract tests (mb#105)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from makerbench.schema import (
    VIDEO_PROMPT_INIT_END_S,
    VIDEO_TIMELAPSE_CORE_END_S,
    VideoEvidence,
    VideoSegment,
    WorkflowManifest,
)
from makerbench.video_evidence import (
    assess_video_protocol,
    video_evidence_meets_official_bar,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _protocol_segments(duration: float = 540.0) -> list[VideoSegment]:
    """A well-formed 3-part protocol covering [0, duration]."""
    return [
        VideoSegment(phase="prompt_init", start_seconds=0.0, end_seconds=VIDEO_PROMPT_INIT_END_S),
        VideoSegment(
            phase="timelapse_core",
            start_seconds=VIDEO_PROMPT_INIT_END_S,
            end_seconds=VIDEO_TIMELAPSE_CORE_END_S,
        ),
        VideoSegment(
            phase="deterministic_verdict",
            start_seconds=VIDEO_TIMELAPSE_CORE_END_S,
            end_seconds=duration,
        ),
    ]


def _evidence(**overrides) -> VideoEvidence:
    base = dict(
        hosted_url="https://videos.makerbench.dev/runs/vented_plate-0.mp4",
        capture_mode="composited",
        sha256="d" * 64,
        duration_seconds=540.0,
        segments=_protocol_segments(),
    )
    base.update(overrides)
    return VideoEvidence(**base)


# --- model ------------------------------------------------------------------


def test_video_evidence_is_optional_and_additive_on_manifest():
    m = WorkflowManifest(task_id="vented_plate", seed=0)
    assert m.video_evidence is None


def test_video_evidence_round_trips_through_json():
    m = WorkflowManifest(task_id="vented_plate", seed=3, video_evidence=_evidence())
    restored = WorkflowManifest.model_validate_json(m.model_dump_json())
    assert restored == m
    assert restored.video_evidence.capture_mode == "composited"
    assert [s.phase for s in restored.video_evidence.segments] == [
        "prompt_init",
        "timelapse_core",
        "deterministic_verdict",
    ]


def test_segment_rejects_end_before_start():
    with pytest.raises(ValidationError):
        VideoSegment(phase="prompt_init", start_seconds=60.0, end_seconds=10.0)


def test_capture_mode_is_constrained():
    with pytest.raises(ValidationError):
        VideoEvidence(hosted_url="https://x/v.mp4", capture_mode="webcam")


# --- protocol assessment (disclosure-grade) ---------------------------------


def test_absent_evidence_is_not_applicable_not_a_failure():
    result = assess_video_protocol(None)
    assert result.checks == {"video_present": False}
    assert result.passed is False
    assert result.score == 0.0


def test_well_formed_protocol_passes_all_checks():
    result = assess_video_protocol(_evidence())
    assert result.passed is True
    assert all(result.checks.values())
    assert result.missing_fields == []


def test_out_of_order_phases_flagged():
    segs = _protocol_segments()
    segs[0], segs[1] = segs[1], segs[0]
    result = assess_video_protocol(_evidence(segments=segs))
    assert result.checks["three_phases_in_order"] is False
    assert result.passed is False


def test_gap_between_segments_flagged_as_noncontiguous():
    segs = _protocol_segments()
    # Open a 60s hole between core and verdict.
    segs[2] = VideoSegment(
        phase="deterministic_verdict", start_seconds=540.0, end_seconds=600.0
    )
    result = assess_video_protocol(_evidence(segments=segs, duration_seconds=600.0))
    assert result.checks["segments_contiguous"] is False


def test_verdict_must_reach_end_of_recording():
    segs = _protocol_segments(duration=540.0)
    # Recording is actually longer than the verdict segment claims.
    result = assess_video_protocol(_evidence(segments=segs, duration_seconds=700.0))
    assert result.checks["verdict_reaches_end"] is False


def test_missing_hash_and_duration_are_surfaced():
    result = assess_video_protocol(_evidence(sha256=None, duration_seconds=None))
    assert result.checks["integrity_hash_present"] is False
    assert result.checks["duration_declared"] is False
    assert "workflow_manifest.video_evidence.sha256" in result.missing_fields


def test_small_marker_drift_within_tolerance_still_passes():
    # A few seconds off the nominal cuts must not penalize a real recording.
    segs = [
        VideoSegment(phase="prompt_init", start_seconds=0.0, end_seconds=58.0),
        VideoSegment(phase="timelapse_core", start_seconds=58.0, end_seconds=478.0),
        VideoSegment(phase="deterministic_verdict", start_seconds=478.0, end_seconds=541.0),
    ]
    result = assess_video_protocol(_evidence(segments=segs, duration_seconds=541.0))
    assert result.passed is True


# --- official-verified bar --------------------------------------------------


def test_official_bar_requires_full_protocol():
    assert video_evidence_meets_official_bar(_evidence()) is True


def test_official_bar_rejects_missing_hash():
    assert video_evidence_meets_official_bar(_evidence(sha256=None)) is False


def test_official_bar_rejects_absent_evidence():
    assert video_evidence_meets_official_bar(None) is False


def test_official_bar_rejects_incomplete_protocol():
    one_segment = _evidence(segments=[_protocol_segments()[0]], duration_seconds=60.0)
    assert video_evidence_meets_official_bar(one_segment) is False


# --- exported schema + example ----------------------------------------------


def test_committed_example_manifest_carries_video_evidence():
    example = json.loads(
        (REPO_ROOT / "schemas" / "examples" / "workflow_manifest.example.json").read_text()
    )
    manifest = WorkflowManifest.model_validate(example)
    assert manifest.video_evidence is not None
    assert video_evidence_meets_official_bar(manifest.video_evidence) is True


def test_exported_schema_includes_video_evidence_property():
    schema = json.loads(
        (REPO_ROOT / "schemas" / "workflow_manifest.schema.json").read_text()
    )
    assert "video_evidence" in schema["properties"]
