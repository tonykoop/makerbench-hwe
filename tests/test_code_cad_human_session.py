from __future__ import annotations

import json
from pathlib import Path

import pytest

from makerbench.code_cad_human_session import (
    HumanAuthoringConfig,
    start_human_session,
    write_human_submission,
)
from makerbench.schema import TaskSpec


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _instrument_spec() -> TaskSpec:
    return TaskSpec(
        task_id="simple_flute_body",
        seed=7,
        params={"length_mm": 180, "bore_mm": 12, "hole_count": 6},
        brief="Author an OpenSCAD flute body with six tone holes.",
        allowed_tools=[],
    )


def _config(**overrides) -> HumanAuthoringConfig:
    values = {
        "session_id": "human-session-001",
        "task_id": "simple_flute_body",
        "seed": 7,
        "budget_seconds": 120.0,
        "entrant_id": "human-tony",
    }
    values.update(overrides)
    return HumanAuthoringConfig(**values)


def test_hard_countdown_auto_submits_at_timeout():
    clock = FakeClock()
    session = start_human_session(_config(budget_seconds=10), _instrument_spec(), clock=clock)
    session.record_edit("cylinder(h=180, d=12);", clock=clock)

    clock.advance(9.9)
    assert session.auto_submit_if_due("// still editing", clock=clock) is None

    clock.advance(0.2)
    manifest = session.auto_submit_if_due("// final buffer", clock=clock)

    assert manifest is not None
    assert manifest["time_budget"]["timed_out"] is True
    assert manifest["time_budget"]["submit_reason"] == "timeout"
    assert manifest["time_budget"]["active_authoring_seconds"] == 10.0
    assert manifest["source"]["sha256"] == manifest["arena_entry"]["submission_sha256"]


def test_manual_submit_records_time_budget_and_edit_trace():
    clock = FakeClock()
    session = start_human_session(_config(budget_seconds=300), _instrument_spec(), clock=clock)

    clock.advance(15)
    first = session.record_edit("module body(){ cylinder(h=180, d=12); }", clock=clock)
    clock.advance(5)
    preview = session.record_preview(
        "module body(){ cylinder(h=180, d=12); } body();",
        clock=clock,
        note="preview rendered locally",
    )
    clock.advance(20)
    final_source = "difference(){ cylinder(h=180, d=12); cylinder(h=181, d=9); }"
    manifest = session.submit(final_source, clock=clock)

    assert manifest["time_budget"]["timed_out"] is False
    assert manifest["time_budget"]["time_to_submit_seconds"] == 40.0
    assert manifest["time_budget"]["budget_seconds"] == 300.0
    assert [event["event_type"] for event in manifest["edit_trace"]] == ["edit", "preview"]
    assert manifest["edit_trace"][0]["offset_seconds"] == 15.0
    assert manifest["edit_trace"][1]["note"] == "preview rendered locally"
    assert first.source_sha256 != preview.source_sha256
    assert "module body" not in json.dumps(manifest["edit_trace"])


def test_edit_after_deadline_requires_auto_submit_current_buffer():
    clock = FakeClock()
    session = start_human_session(_config(budget_seconds=2), _instrument_spec(), clock=clock)

    clock.advance(2)
    with pytest.raises(TimeoutError, match="budget expired"):
        session.record_edit("late source", clock=clock)

    manifest = session.auto_submit_if_due("late source", clock=clock)
    assert manifest["time_budget"]["timed_out"] is True
    assert manifest["edit_trace"] == []
    assert manifest["workflow_provenance"]["human_intervention_index"]["highest_level"] == "L2"


def test_submission_enters_blind_arena_and_objective_gate():
    clock = FakeClock()
    session = start_human_session(_config(track="blind"), _instrument_spec(), clock=clock)
    clock.advance(12)
    manifest = session.submit("cylinder(h=180, d=12);", clock=clock)

    assert manifest["entrant"]["entrant_type"] == "human-solo"
    assert manifest["entrant"]["harness_class"] == "assisted-workflow"
    assert manifest["arena_entry"] == {
        "entry_id": "human-session-001:human-tony",
        "entrant_id": "human-tony",
        "entrant_type": "human-solo",
        "task_id": "simple_flute_body",
        "seed": 7,
        "track": "blind",
        "submission_sha256": manifest["source"]["sha256"],
        "blind_label": None,
    }
    assert manifest["objective_gate_entry"] == {
        "task_id": "simple_flute_body",
        "seed": 7,
        "track": "blind",
        "artifact_sha256": manifest["source"]["sha256"],
        "source_language": "openscad",
    }


def test_persisted_session_writes_source_and_metadata_for_one_spec(tmp_path: Path):
    clock = FakeClock()
    session = start_human_session(_config(), _instrument_spec(), clock=clock)
    clock.advance(3)
    source = "difference(){ cylinder(h=180, d=12); cylinder(h=180, d=9); }"

    persisted = write_human_submission(session, source, tmp_path, clock=clock)
    source_path = Path(persisted["paths"]["source"])
    manifest_path = Path(persisted["paths"]["manifest"])
    saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert source_path.read_text(encoding="utf-8") == source
    assert saved_manifest["task_id"] == "simple_flute_body"
    assert saved_manifest["spec"]["params"]["hole_count"] == 6
    assert saved_manifest["arena_entry"]["entrant_type"] == "human-solo"
    assert saved_manifest["objective_gate_entry"]["source_language"] == "openscad"


def test_config_rejects_non_positive_budget():
    with pytest.raises(ValueError, match="budget_seconds must be positive"):
        _config(budget_seconds=0)
