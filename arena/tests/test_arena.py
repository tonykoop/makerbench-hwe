"""
Tests for arena.arena — orchestration, batch generation, and Elo integration.

All run offline (mock generator only, no API keys, no openscad).
"""

from __future__ import annotations

import pytest

from arena.arena import (
    ArenaConfig,
    ArenaRun,
    DEFAULT_AI_ENTRANTS,
    process_votes,
    run_generation_batch,
    run_render_phase,
)
from arena.elo import EloEngine
from arena.generator import MockGenerator
from arena.models import Entrant, EntrantType, MatchResult, VoteOutcome
from arena.specs import EXAMPLE_SPECS, get_spec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_only_config() -> ArenaConfig:
    """Config with only the mock entrant and one spec — fast, offline."""
    return ArenaConfig(
        entrants=[Entrant(entrant_id="mock", entrant_type=EntrantType.AI)],
        specs=[get_spec("dulcimer-3string-v1")],
        seeds=[42],
        generators=[MockGenerator()],
        render_outputs=False,
    )


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------


class TestRunGenerationBatch:
    def test_produces_trial_results(self, mock_only_config):
        run = run_generation_batch(mock_only_config)
        assert len(run.trial_results) == 1
        assert run.trial_results[0].succeeded

    def test_result_provenance_correct(self, mock_only_config):
        run = run_generation_batch(mock_only_config)
        t = run.trial_results[0]
        assert t.spec_id == "dulcimer-3string-v1"
        assert t.entrant_id == "mock"
        assert t.seed == 42

    def test_multiple_seeds_produce_multiple_results(self):
        cfg = ArenaConfig(
            entrants=[Entrant(entrant_id="mock")],
            specs=[get_spec("dulcimer-3string-v1")],
            seeds=[1, 2, 3],
            generators=[MockGenerator()],
        )
        run = run_generation_batch(cfg)
        assert len(run.trial_results) == 3

    def test_multiple_specs_produce_multiple_results(self):
        cfg = ArenaConfig(
            entrants=[Entrant(entrant_id="mock")],
            specs=EXAMPLE_SPECS,
            seeds=[42],
            generators=[MockGenerator()],
        )
        run = run_generation_batch(cfg)
        assert len(run.trial_results) == len(EXAMPLE_SPECS)

    def test_no_generator_for_entrant_records_nothing(self):
        """Entrant with no matching generator: skip silently, no crash."""
        cfg = ArenaConfig(
            entrants=[Entrant(entrant_id="unknown-model")],
            specs=[get_spec("dulcimer-3string-v1")],
            seeds=[42],
            generators=[MockGenerator()],  # only handles "mock"
        )
        run = run_generation_batch(cfg)
        # No result expected (generator missing → skipped)
        assert len(run.trial_results) == 0

    def test_human_entrant_skipped_gracefully(self):
        """Human entrants (#428) are skipped in generation batch — not an error."""
        human = Entrant(
            entrant_id="human:tony",
            entrant_type=EntrantType.HUMAN,
            time_budget_seconds=300.0,
        )
        cfg = ArenaConfig(
            entrants=[human, Entrant(entrant_id="mock")],
            specs=[get_spec("dulcimer-3string-v1")],
            seeds=[42],
            generators=[MockGenerator()],
        )
        run = run_generation_batch(cfg)
        # Only the mock AI entrant should produce a result
        assert all(t.entrant_id == "mock" for t in run.trial_results)

    def test_default_config_uses_mock_only(self):
        """Default ArenaConfig includes mock; CI must have at least one result."""
        cfg = ArenaConfig(
            entrants=[Entrant(entrant_id="mock")],
            specs=[get_spec("dulcimer-3string-v1")],
            seeds=[42],
            generators=[MockGenerator()],
        )
        run = run_generation_batch(cfg)
        assert len(run.trial_results) >= 1

    def test_unavailable_generator_records_skipped_trial(self, monkeypatch):
        """Unavailable generator: trial recorded with error, not crashing."""
        from arena.generator import AnthropicGenerator

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = ArenaConfig(
            entrants=[Entrant(entrant_id="claude-sonnet")],
            specs=[get_spec("dulcimer-3string-v1")],
            seeds=[42],
            generators=[AnthropicGenerator()],
        )
        run = run_generation_batch(cfg)
        assert len(run.trial_results) == 1
        assert not run.trial_results[0].succeeded
        assert run.trial_results[0].error

    def test_successful_and_failed_trial_accessors(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from arena.generator import AnthropicGenerator

        cfg = ArenaConfig(
            entrants=[
                Entrant(entrant_id="mock"),
                Entrant(entrant_id="claude-sonnet"),
            ],
            specs=[get_spec("dulcimer-3string-v1")],
            seeds=[42],
            generators=[MockGenerator(), AnthropicGenerator()],
        )
        run = run_generation_batch(cfg)
        assert len(run.successful_trials()) >= 1
        assert len(run.failed_trials()) >= 1


# ---------------------------------------------------------------------------
# Render phase — auto-fail path
# ---------------------------------------------------------------------------


class TestRunRenderPhase:
    def test_render_skipped_when_flag_false(self, mock_only_config, monkeypatch):
        """render_outputs=False: render_results stays empty."""
        run = run_generation_batch(mock_only_config)
        run_render_phase(run, mock_only_config)
        assert run.render_results == {}

    def test_render_auto_fail_when_openscad_absent(self, mock_only_config, monkeypatch):
        """openscad absent → auto-fail recorded, no exception."""
        monkeypatch.setattr("shutil.which", lambda _: None)
        mock_only_config.render_outputs = True

        run = run_generation_batch(mock_only_config)
        run_render_phase(run, mock_only_config)  # must not raise

        # At least one render result should be OPENSCAD_NOT_FOUND
        from arena.render import RenderStatus
        statuses = {r.status for r in run.render_results.values()}
        assert RenderStatus.OPENSCAD_NOT_FOUND in statuses


# ---------------------------------------------------------------------------
# Elo integration
# ---------------------------------------------------------------------------


class TestProcessVotes:
    def test_votes_update_elo_engine(self, mock_only_config):
        run = run_generation_batch(mock_only_config)
        assert run.elo_engine is not None

        matches = [
            MatchResult(
                spec_id="dulcimer-3string-v1",
                seed=42,
                entrant_a_id="mock",
                entrant_b_id="gpt-4o",
                outcome=VoteOutcome.A_WINS,
            )
        ]
        process_votes(run, matches)

        lb = run.leaderboard
        assert lb is not None
        assert lb.total_matches == 1

    def test_leaderboard_reflects_votes(self, mock_only_config):
        run = run_generation_batch(mock_only_config)

        # mock beats gpt-4o 3 times
        matches = [
            MatchResult("dulcimer", 42, "mock", "gpt-4o", VoteOutcome.A_WINS),
            MatchResult("dulcimer", 43, "mock", "gpt-4o", VoteOutcome.A_WINS),
            MatchResult("dulcimer", 44, "mock", "gpt-4o", VoteOutcome.A_WINS),
        ]
        process_votes(run, matches)
        lb = run.leaderboard
        assert lb.ratings[0].entrant_id == "mock"

    def test_human_entrant_ranks_with_models(self):
        """Entrant-generic: human:tony competes on the same leaderboard as models."""
        run = ArenaRun(elo_engine=EloEngine())
        matches = [
            MatchResult("spec", 1, "human:tony", "gpt-4o", VoteOutcome.A_WINS),
            MatchResult("spec", 2, "human:tony", "claude-sonnet", VoteOutcome.A_WINS),
            MatchResult("spec", 3, "gpt-4o", "claude-sonnet", VoteOutcome.A_WINS),
        ]
        process_votes(run, matches)
        lb = run.leaderboard
        assert lb.ratings[0].entrant_id == "human:tony"
        entrant_ids = [r.entrant_id for r in lb.ratings]
        assert "gpt-4o" in entrant_ids
        assert "claude-sonnet" in entrant_ids
