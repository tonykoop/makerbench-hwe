from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench.code_cad_copilot_session import (
    CopilotSessionConfig,
    build_scoring_entries,
    hii_counts,
    start_copilot_session,
    write_copilot_submission,
)
from makerbench.schema import TaskSpec


class FakeClock:
    def __init__(self, now: float = 500.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _instrument_spec() -> TaskSpec:
    return TaskSpec(
        task_id="simple_flute_body",
        seed=3,
        params={"length_mm": 210, "bore_mm": 14, "hole_count": 6},
        brief="Refine an OpenSCAD flute body from an AI draft.",
        allowed_tools=[],
    )


def _config(**overrides) -> CopilotSessionConfig:
    values = {
        "session_id": "copilot-session-001",
        "task_id": "simple_flute_body",
        "seed": 3,
        "budget_seconds": 300.0,
        "human_id": "human-tony",
        "ai_model_id": "cad-draft-model",
        "entrant_id": "tony-plus-cad-draft",
    }
    values.update(overrides)
    return CopilotSessionConfig(**values)


def test_ai_draft_setup_is_separate_from_active_budget():
    clock = FakeClock(now=1000)
    session = start_copilot_session(
        _config(budget_seconds=60),
        _instrument_spec(),
        "cylinder(h=210, d=14);",
        draft_started_at=970,
        clock=clock,
        prompt="make a flute body",
    )

    clock.advance(15)
    manifest = session.submit("cylinder(h=210, d=14);", clock=clock)

    assert manifest["setup"]["ai_draft"]["setup_seconds"] == 30.0
    assert manifest["setup"]["counts_against_budget"] is False
    assert manifest["time_budget"]["active_authoring_seconds"] == 15.0
    assert manifest["setup"]["ai_draft"]["prompt_sha256"] is not None


def test_human_edit_and_ai_suggestion_share_countdown_and_trace():
    clock = FakeClock()
    session = start_copilot_session(
        _config(budget_seconds=120),
        _instrument_spec(),
        "cylinder(h=210, d=14);",
        draft_started_at=490,
        clock=clock,
    )

    clock.advance(10)
    edit = session.record_human_edit(
        "difference(){ cylinder(h=210, d=14); cylinder(h=210, d=10); }",
        clock=clock,
        note="opened bore",
    )
    clock.advance(20)
    suggestion = session.record_ai_suggestion(
        "add six tone holes with for-loop",
        accepted=False,
        clock=clock,
        note="hole placement was not constrained",
    )
    clock.advance(5)
    final_source = "difference(){ cylinder(h=210, d=14); cylinder(h=210, d=10); }"
    manifest = session.submit(final_source, clock=clock)

    assert manifest["time_budget"]["time_to_submit_seconds"] == 35.0
    assert [event["event_type"] for event in manifest["intervention_trace"]] == [
        "human_edit",
        "ai_suggestion",
    ]
    assert manifest["intervention_trace"][0]["note"] == "opened bore"
    assert manifest["intervention_trace"][1]["accepted"] is False
    assert edit.source_sha256 != suggestion.source_sha256
    assert "difference()" not in json.dumps(manifest["intervention_trace"])


def test_timeout_auto_submits_current_copilot_buffer():
    clock = FakeClock()
    session = start_copilot_session(
        _config(budget_seconds=5),
        _instrument_spec(),
        "cylinder(h=210, d=14);",
        draft_started_at=499,
        clock=clock,
    )

    clock.advance(4.9)
    assert session.auto_submit_if_due("draft plus edits", clock=clock) is None
    clock.advance(0.2)
    manifest = session.auto_submit_if_due("draft plus edits", clock=clock)

    assert manifest is not None
    assert manifest["time_budget"]["timed_out"] is True
    assert manifest["time_budget"]["active_authoring_seconds"] == 5.0
    assert manifest["time_budget"]["submit_reason"] == "timeout"


def test_hii_counts_are_workflow_manifest_compatible():
    clock = FakeClock()
    session = start_copilot_session(
        _config(),
        _instrument_spec(),
        "cylinder(h=210, d=14);",
        draft_started_at=490,
        clock=clock,
    )
    clock.advance(1)
    session.record_ai_suggestion("make wall thinner", accepted=True, clock=clock)
    clock.advance(1)
    session.record_human_edit("difference(){ cylinder(h=210, d=14); }", clock=clock)

    counts = hii_counts(session.intervention_trace, autonomous_events=1)
    manifest = session.submit("difference(){ cylinder(h=210, d=14); }", clock=clock)

    assert counts == {
        "l0_autonomous_events": 1,
        "l1_nl_steering_events": 1,
        "l2_copilot_manual_events": 1,
        "autonomy_ratio": 0.5,
        "highest_level": "L2",
    }
    assert manifest["workflow_provenance"]["human_intervention_index"] == counts


def test_all_three_entrant_types_use_identical_scoring_shape():
    entries = {
        kind: build_scoring_entries(
            session_id="s",
            entrant_id=f"entrant-{kind}",
            entrant_type=kind,
            task_id="simple_flute_body",
            seed=3,
            track="blind",
            source_sha256="a" * 64,
        )
        for kind in ("ai-solo", "human-solo", "human+ai")
    }

    key_sets = {kind: set(payload.keys()) for kind, payload in entries.items()}
    assert key_sets["ai-solo"] == key_sets["human-solo"] == key_sets["human+ai"]
    assert entries["human+ai"]["arena_entry"]["blind_label"] is None
    assert entries["ai-solo"]["objective_gate_entry"]["source_language"] == "openscad"

    with pytest.raises(ValueError, match="entrant_type"):
        build_scoring_entries(
            session_id="s",
            entrant_id="bad",
            entrant_type="spreadsheet",
            task_id="simple_flute_body",
            seed=3,
            track="blind",
            source_sha256="b" * 64,
        )


def test_persisted_copilot_submission_writes_one_instrument_spec(tmp_path: Path):
    clock = FakeClock()
    session = start_copilot_session(
        _config(),
        _instrument_spec(),
        "cylinder(h=210, d=14);",
        draft_started_at=497,
        clock=clock,
    )
    clock.advance(11)
    source = "difference(){ cylinder(h=210, d=14); cylinder(h=210, d=10); }"

    persisted = write_copilot_submission(session, source, tmp_path, clock=clock)
    source_path = Path(persisted["paths"]["source"])
    manifest_path = Path(persisted["paths"]["manifest"])
    saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert source_path.read_text(encoding="utf-8") == source
    assert saved_manifest["entrant"]["entrant_type"] == "human+ai"
    assert saved_manifest["comparison_contract"]["entrant_types"] == [
        "ai-solo",
        "human-solo",
        "human+ai",
    ]
    assert saved_manifest["spec"]["params"]["length_mm"] == 210


def test_config_rejects_missing_ai_model():
    with pytest.raises(ValueError, match="ai_model_id is required"):
        _config(ai_model_id="")
