"""Issue #105 acceptance lock for video / screen-recording evidence."""

from __future__ import annotations

import json
from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "VIDEO_EVIDENCE.md"
SCHEMA = ROOT / "schemas" / "workflow_manifest.schema.json"


def _segments(duration: float = 540.0) -> list[VideoSegment]:
    return [
        VideoSegment(
            phase="prompt_init",
            start_seconds=0.0,
            end_seconds=VIDEO_PROMPT_INIT_END_S,
            marker="Prompt Init",
        ),
        VideoSegment(
            phase="timelapse_core",
            start_seconds=VIDEO_PROMPT_INIT_END_S,
            end_seconds=VIDEO_TIMELAPSE_CORE_END_S,
            marker="Time-lapse Core",
        ),
        VideoSegment(
            phase="deterministic_verdict",
            start_seconds=VIDEO_TIMELAPSE_CORE_END_S,
            end_seconds=duration,
            marker="Deterministic Verdict",
        ),
    ]


def _evidence(**overrides) -> VideoEvidence:
    data = {
        "hosted_url": "https://videos.makerbench.dev/runs/demo.mp4",
        "capture_mode": "composited",
        "sha256": "d" * 64,
        "duration_seconds": 540.0,
        "segments": _segments(),
    }
    data.update(overrides)
    return VideoEvidence(**data)


def test_story_105_video_evidence_role_round_trips_on_workflow_manifest():
    manifest = WorkflowManifest(task_id="vented_plate", seed=3, video_evidence=_evidence())
    restored = WorkflowManifest.model_validate_json(manifest.model_dump_json())

    assert restored.video_evidence is not None
    assert restored.video_evidence.hosted_url.startswith("https://")
    assert restored.video_evidence.capture_mode == "composited"
    assert restored.video_evidence.sha256 == "d" * 64
    assert restored.video_evidence.duration_seconds == 540.0
    assert [seg.phase for seg in restored.video_evidence.segments] == [
        "prompt_init",
        "timelapse_core",
        "deterministic_verdict",
    ]


def test_story_105_three_part_protocol_windows_are_lightly_validated():
    result = assess_video_protocol(_evidence())

    assert result.category == "video_evidence"
    assert result.passed is True
    assert result.checks["three_phases_in_order"] is True
    assert result.checks["segments_contiguous"] is True
    assert result.checks["prompt_init_window"] is True
    assert result.checks["verdict_reaches_end"] is True
    assert result.missing_fields == []


def test_story_105_protocol_flags_missing_hash_gap_and_bad_verdict_end():
    gap_segments = _segments(duration=600.0)
    gap_segments[2] = VideoSegment(
        phase="deterministic_verdict",
        start_seconds=540.0,
        end_seconds=600.0,
    )
    gap = assess_video_protocol(_evidence(segments=gap_segments, duration_seconds=600.0))
    no_hash = assess_video_protocol(_evidence(sha256=None))
    short_verdict = assess_video_protocol(_evidence(duration_seconds=700.0))

    assert gap.checks["segments_contiguous"] is False
    assert no_hash.checks["integrity_hash_present"] is False
    assert "workflow_manifest.video_evidence.sha256" in no_hash.missing_fields
    assert short_verdict.checks["verdict_reaches_end"] is False


def test_story_105_official_verified_bar_is_not_a_geometry_gate():
    assert assess_video_protocol(None).checks == {"video_present": False}
    assert video_evidence_meets_official_bar(None) is False
    assert video_evidence_meets_official_bar(_evidence()) is True
    assert video_evidence_meets_official_bar(_evidence(sha256=None)) is False


def test_story_105_docs_and_schema_expose_contract_fields():
    doc = DOC.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert "video_evidence" in schema["properties"]
    for needle in (
        "hosted URL",
        "sha256",
        "duration",
        "capture mode",
        "`00:00–01:00`",
        "`01:00–08:00`",
        "`08:00–end`",
        "Official Verified",
        "never a hard gate",
    ):
        assert needle in doc
