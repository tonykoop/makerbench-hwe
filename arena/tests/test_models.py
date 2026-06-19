"""Tests for arena.models — data model construction and constraints."""

from __future__ import annotations

import pytest

from arena.models import (
    Entrant,
    EntrantType,
    InstrumentSpec,
    MatchResult,
    TrialResult,
    VoteOutcome,
)


class TestEntrant:
    def test_ai_entrant_defaults(self):
        e = Entrant(entrant_id="gpt-4o")
        assert e.entrant_type == EntrantType.AI
        assert e.display_name == "gpt-4o"
        assert e.time_budget_seconds is None

    def test_human_entrant_with_budget(self):
        e = Entrant(
            entrant_id="human:tony",
            entrant_type=EntrantType.HUMAN,
            display_name="Tony (5-min)",
            time_budget_seconds=300.0,
        )
        assert e.entrant_type == EntrantType.HUMAN
        assert e.time_budget_seconds == 300.0

    def test_human_ai_entrant(self):
        e = Entrant(
            entrant_id="human+ai:tony",
            entrant_type=EntrantType.HUMAN_AI,
            time_budget_seconds=600.0,
        )
        assert e.entrant_type == EntrantType.HUMAN_AI
        assert "600" in str(e)

    def test_display_name_auto_filled(self):
        e = Entrant(entrant_id="claude-sonnet")
        assert e.display_name == "claude-sonnet"

    def test_explicit_display_name_not_overridden(self):
        e = Entrant(entrant_id="claude-sonnet", display_name="Claude Sonnet 4.5")
        assert e.display_name == "Claude Sonnet 4.5"

    def test_frozen_immutability(self):
        e = Entrant(entrant_id="mock")
        with pytest.raises((AttributeError, TypeError)):
            e.entrant_id = "changed"  # type: ignore[misc]


class TestInstrumentSpec:
    def test_basic_construction(self):
        spec = InstrumentSpec(
            spec_id="test-instrument",
            instrument_family="chordophone",
            parameters={"body_length_mm": 400, "string_count": 3},
        )
        assert spec.spec_id == "test-instrument"
        assert spec.parameters["string_count"] == 3

    def test_content_hash_is_stable(self):
        spec = InstrumentSpec(
            spec_id="dulcimer",
            instrument_family="chordophone",
            parameters={"body_length_mm": 660},
        )
        h1 = spec.content_hash()
        h2 = spec.content_hash()
        assert h1 == h2
        assert len(h1) == 16  # truncated SHA-256

    def test_content_hash_changes_with_params(self):
        base = InstrumentSpec(
            spec_id="dulcimer",
            instrument_family="chordophone",
            parameters={"body_length_mm": 660},
        )
        modified = InstrumentSpec(
            spec_id="dulcimer",
            instrument_family="chordophone",
            parameters={"body_length_mm": 700},
        )
        assert base.content_hash() != modified.content_hash()

    def test_frozen(self):
        spec = InstrumentSpec(
            spec_id="test", instrument_family="aerophone", parameters={}
        )
        with pytest.raises((AttributeError, TypeError)):
            spec.spec_id = "changed"  # type: ignore[misc]


class TestTrialResult:
    def test_succeeded_when_source_present(self):
        t = TrialResult(
            spec_id="s1",
            entrant_id="mock",
            entrant_type=EntrantType.AI,
            seed=42,
            scad_source="// some scad",
        )
        assert t.succeeded is True

    def test_failed_when_no_source(self):
        t = TrialResult(
            spec_id="s1",
            entrant_id="mock",
            entrant_type=EntrantType.AI,
            seed=42,
            scad_source=None,
            error="API timeout",
        )
        assert t.succeeded is False

    def test_provenance_key_format(self):
        t = TrialResult(
            spec_id="dulcimer-3string-v1",
            entrant_id="gpt-4o",
            entrant_type=EntrantType.AI,
            seed=99,
        )
        key = t.provenance_key()
        assert "dulcimer-3string-v1" in key
        assert "gpt-4o" in key
        assert "99" in key

    def test_time_budget_fields_optional(self):
        """time_budget_seconds and time_to_submit_seconds are None for AI runs."""
        t = TrialResult(
            spec_id="s",
            entrant_id="mock",
            entrant_type=EntrantType.AI,
            seed=1,
        )
        assert t.time_budget_seconds is None
        assert t.time_to_submit_seconds is None

    def test_time_budget_fields_populated_for_human(self):
        """Human sessions (#428) should populate both fields."""
        t = TrialResult(
            spec_id="s",
            entrant_id="human:tony",
            entrant_type=EntrantType.HUMAN,
            seed=1,
            time_budget_seconds=300.0,
            time_to_submit_seconds=240.5,
        )
        assert t.time_budget_seconds == 300.0
        assert t.time_to_submit_seconds == 240.5


class TestMatchResult:
    def test_construction(self):
        m = MatchResult(
            spec_id="dulcimer-3string-v1",
            seed=42,
            entrant_a_id="gpt-4o",
            entrant_b_id="claude-sonnet",
            outcome=VoteOutcome.B_WINS,
        )
        assert m.outcome == VoteOutcome.B_WINS
        assert m.voter_id == "tony"  # default

    def test_tie_outcome(self):
        m = MatchResult(
            spec_id="s",
            seed=1,
            entrant_a_id="a",
            entrant_b_id="b",
            outcome=VoteOutcome.TIE,
        )
        assert m.outcome == VoteOutcome.TIE
