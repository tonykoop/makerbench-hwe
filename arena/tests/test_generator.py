"""
Tests for arena.generator — harness, MockGenerator determinism, and adapter stubs.

All tests run offline (no API keys, no network).  Real-model adapters are tested
only through their skip-path (is_available() → False when env var absent).
"""

from __future__ import annotations

import os

import pytest

from arena.generator import (
    AnthropicGenerator,
    GeneratorResult,
    GoogleGenerator,
    MockGenerator,
    OpenAIGenerator,
    get_generator_for,
)
from arena.models import Entrant, EntrantType, InstrumentSpec
from arena.specs import get_spec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dulcimer_spec() -> InstrumentSpec:
    return get_spec("dulcimer-3string-v1")


@pytest.fixture
def mock_entrant() -> Entrant:
    return Entrant(entrant_id="mock", entrant_type=EntrantType.AI)


@pytest.fixture
def mock_gen() -> MockGenerator:
    return MockGenerator()


# ---------------------------------------------------------------------------
# MockGenerator — determinism
# ---------------------------------------------------------------------------


class TestMockGeneratorDeterminism:
    def test_same_spec_seed_produces_same_output(
        self, mock_gen, dulcimer_spec, mock_entrant
    ):
        """Core determinism requirement from story #422 acceptance criteria."""
        r1 = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        r2 = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        assert r1.scad_source == r2.scad_source

    def test_different_seeds_produce_different_output(
        self, mock_gen, dulcimer_spec, mock_entrant
    ):
        """Seed must affect output so trials are distinguishable."""
        r1 = mock_gen.generate(dulcimer_spec, mock_entrant, seed=1)
        r2 = mock_gen.generate(dulcimer_spec, mock_entrant, seed=2)
        assert r1.scad_source != r2.scad_source

    def test_different_specs_produce_different_output(self, mock_gen, mock_entrant):
        spec_a = get_spec("dulcimer-3string-v1")
        spec_b = get_spec("ukulele-soprano-v1")
        r1 = mock_gen.generate(spec_a, mock_entrant, seed=42)
        r2 = mock_gen.generate(spec_b, mock_entrant, seed=42)
        assert r1.scad_source != r2.scad_source

    def test_output_contains_spec_id(self, mock_gen, dulcimer_spec, mock_entrant):
        r = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        assert dulcimer_spec.spec_id in (r.scad_source or "")

    def test_output_contains_seed(self, mock_gen, dulcimer_spec, mock_entrant):
        r = mock_gen.generate(dulcimer_spec, mock_entrant, seed=77)
        assert "77" in (r.scad_source or "")

    def test_output_is_openscad_like(self, mock_gen, dulcimer_spec, mock_entrant):
        """Basic heuristic: output should contain OpenSCAD syntax tokens."""
        r = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        scad = r.scad_source or ""
        assert "module" in scad or "union" in scad or "difference" in scad

    def test_always_succeeds(self, mock_gen, dulcimer_spec, mock_entrant):
        r = mock_gen.generate(dulcimer_spec, mock_entrant, seed=0)
        assert r.succeeded
        assert r.error is None

    def test_latency_recorded(self, mock_gen, dulcimer_spec, mock_entrant):
        r = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        assert r.latency_seconds is not None
        assert r.latency_seconds >= 0.0

    def test_metadata_populated(self, mock_gen, dulcimer_spec, mock_entrant):
        r = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        assert r.metadata.get("generator") == "MockGenerator"
        assert "spec_hash" in r.metadata

    def test_all_example_specs(self, mock_gen, mock_entrant):
        """MockGenerator must handle every example spec without error."""
        from arena.specs import EXAMPLE_SPECS
        for spec in EXAMPLE_SPECS:
            r = mock_gen.generate(spec, mock_entrant, seed=42)
            assert r.succeeded, f"Failed for spec {spec.spec_id}: {r.error}"

    def test_seed_determinism_across_specs(self, mock_gen, mock_entrant):
        """Cross-spec: same seed produces a stable hash per spec."""
        from arena.specs import EXAMPLE_SPECS
        outputs_first_run = {
            s.spec_id: mock_gen.generate(s, mock_entrant, seed=99).scad_source
            for s in EXAMPLE_SPECS
        }
        outputs_second_run = {
            s.spec_id: mock_gen.generate(s, mock_entrant, seed=99).scad_source
            for s in EXAMPLE_SPECS
        }
        assert outputs_first_run == outputs_second_run


# ---------------------------------------------------------------------------
# GeneratorResult → TrialResult conversion
# ---------------------------------------------------------------------------


class TestGeneratorResultConversion:
    def test_to_trial_result_success(self, mock_gen, dulcimer_spec, mock_entrant):
        gen_result = mock_gen.generate(dulcimer_spec, mock_entrant, seed=42)
        trial = gen_result.to_trial_result(mock_entrant)
        assert trial.spec_id == dulcimer_spec.spec_id
        assert trial.entrant_id == mock_entrant.entrant_id
        assert trial.seed == 42
        assert trial.scad_source == gen_result.scad_source
        assert trial.succeeded

    def test_to_trial_result_carries_entrant_type(self, mock_gen, dulcimer_spec):
        human_entrant = Entrant(
            entrant_id="human:tony",
            entrant_type=EntrantType.HUMAN,
            time_budget_seconds=300.0,
        )
        gen_result = GeneratorResult(
            spec_id=dulcimer_spec.spec_id,
            entrant_id="human:tony",
            seed=1,
            scad_source="// human scad",
        )
        trial = gen_result.to_trial_result(human_entrant)
        assert trial.entrant_type == EntrantType.HUMAN
        assert trial.time_budget_seconds == 300.0


# ---------------------------------------------------------------------------
# Dispatch — get_generator_for
# ---------------------------------------------------------------------------


class TestGetGeneratorFor:
    def test_dispatches_to_mock(self):
        e = Entrant(entrant_id="mock")
        gen = get_generator_for(e)
        assert isinstance(gen, MockGenerator)

    def test_returns_none_for_unknown_entrant(self):
        e = Entrant(entrant_id="unknown-model-xyz")
        gen = get_generator_for(e, generators=[MockGenerator()])
        assert gen is None

    def test_custom_generator_list(self):
        e = Entrant(entrant_id="mock")
        gen = get_generator_for(e, generators=[MockGenerator()])
        assert gen is not None


# ---------------------------------------------------------------------------
# Real-model stubs — skip-path when API keys absent
# ---------------------------------------------------------------------------


class TestRealModelAdapterSkipPaths:
    """These tests verify the skip-path only — no real API calls."""

    def _ensure_key_absent(self, key: str):
        """Remove a key from env for the duration of a test."""
        return pytest.MonkeyPatch().delitem(os.environ, key, raising=False)

    @pytest.mark.parametrize(
        "generator_cls, env_key",
        [
            (AnthropicGenerator, "ANTHROPIC_API_KEY"),
            (OpenAIGenerator, "OPENAI_API_KEY"),
            (GoogleGenerator, "GOOGLE_API_KEY"),
        ],
    )
    def test_unavailable_when_key_absent(
        self, monkeypatch, generator_cls, env_key, dulcimer_spec
    ):
        monkeypatch.delenv(env_key, raising=False)
        gen = generator_cls()
        assert not gen.is_available()

    @pytest.mark.parametrize(
        "generator_cls, env_key",
        [
            (AnthropicGenerator, "ANTHROPIC_API_KEY"),
            (OpenAIGenerator, "OPENAI_API_KEY"),
            (GoogleGenerator, "GOOGLE_API_KEY"),
        ],
    )
    def test_generate_returns_skipped_result_when_unavailable(
        self, monkeypatch, generator_cls, env_key, dulcimer_spec, mock_entrant
    ):
        """When unavailable, generate() must return a result (not raise)."""
        monkeypatch.delenv(env_key, raising=False)
        gen = generator_cls()
        # Use a fake entrant_id that the generator doesn't own to avoid the
        # "not in supported_entrant_ids" short-circuit — we want the key-check path.
        fake_entrant = Entrant(entrant_id=list(gen.supported_entrant_ids)[0])
        result = gen.generate(dulcimer_spec, fake_entrant, seed=1)
        assert result.scad_source is None
        assert result.error is not None
        assert result.metadata.get("skipped") is True

    def test_anthropic_supported_ids(self):
        gen = AnthropicGenerator()
        assert "claude-sonnet" in gen.supported_entrant_ids

    def test_openai_supported_ids(self):
        gen = OpenAIGenerator()
        assert "gpt-4o" in gen.supported_entrant_ids

    def test_google_supported_ids(self):
        gen = GoogleGenerator()
        assert "gemini" in gen.supported_entrant_ids
