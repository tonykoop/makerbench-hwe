"""
arena.models — Core data models for the Code-CAD A/B Arena.

Design: the central abstraction is an **Entrant** — a model, a human, or a human+AI
pair. This keeps the Elo engine and arena logic entrant-generic so stories #428-430
(time-constrained human tracks) plug in without restructuring.

Each TrialResult records full provenance: (spec_id, model/entrant, seed).
Time-budget fields (time_budget_seconds, time_to_submit_seconds) are optional;
they are None for untimed AI runs and populated for timed human sessions (#428/#429).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EntrantType(str, Enum):
    """Taxonomy of arena entrants.

    ai        — a language model running without human interaction.
    human     — a human author under a time budget (story #428).
    human_ai  — a human with AI co-pilot under a time budget (story #429).
    """

    AI = "ai"
    HUMAN = "human"
    HUMAN_AI = "human_ai"


@dataclass(frozen=True)
class Entrant:
    """An arena competitor — a model, a human, or a human+AI pair.

    Attributes
    ----------
    entrant_id:
        Stable unique identifier. For AI entrants this is usually the model slug
        (e.g. ``"gpt-5.5"``). For human sessions use ``"human:<handle>"``.
    entrant_type:
        Whether this is an AI, human, or human+AI entrant.
    display_name:
        Human-readable label shown on the leaderboard.
    time_budget_seconds:
        Hard limit for the authoring session. None for untimed AI runs (the normal case).
        Populated for story-#428 / #429 human sessions.
    metadata:
        Arbitrary extra fields (model family, temperature, human-track label, etc.).
    """

    entrant_id: str
    entrant_type: EntrantType = EntrantType.AI
    display_name: str = ""
    time_budget_seconds: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Supply a sensible display name if the caller left it blank.
        if not self.display_name:
            object.__setattr__(self, "display_name", self.entrant_id)

    def __str__(self) -> str:
        budget = (
            f" [{int(self.time_budget_seconds)}s]"
            if self.time_budget_seconds is not None
            else ""
        )
        return f"{self.display_name}{budget} ({self.entrant_type.value})"


@dataclass(frozen=True)
class InstrumentSpec:
    """Parametric description of a single instrument to be generated.

    In production this comes from ``instrument-maker`` ``registry.yaml``.
    In the prototype we define example specs inline (see ``arena/specs/``).

    The (spec_id, seed) pair is held constant across entrants in one trial round —
    the only variable is the entrant. This is the core DoE constraint.

    Attributes
    ----------
    spec_id:
        Stable identifier matching the registry entry (e.g. ``"dulcimer-3string-v1"``).
    instrument_family:
        Top-level family tag (``"chordophone"``, ``"aerophone"``, etc.).
    parameters:
        Numeric / string parameters that a generator should embed in the OpenSCAD output.
    description:
        Human-readable summary for display and prompt construction.
    """

    spec_id: str
    instrument_family: str
    parameters: dict[str, Any]
    description: str = ""

    def content_hash(self) -> str:
        """Stable SHA-256 of the spec content (for provenance / dedup)."""
        payload = json.dumps(
            {"spec_id": self.spec_id, "parameters": self.parameters}, sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class TrialResult:
    """Output of one entrant's attempt at generating code for one spec+seed.

    Provenance triple: (spec_id, entrant_id, seed).

    Attributes
    ----------
    spec_id:
        The instrument spec that was attempted.
    entrant_id:
        Which entrant produced this output.
    entrant_type:
        Cached type for downstream filtering without re-joining to Entrant.
    seed:
        Random seed used for this trial (held constant across entrants in a round).
    scad_source:
        The generated OpenSCAD source string, or None if generation failed.
    error:
        Human-readable error message if generation failed.
    generation_latency_seconds:
        Wall-clock time for generation (always populated for AI, optional for human).
    time_budget_seconds:
        Hard budget from the Entrant (None = untimed AI run).
    time_to_submit_seconds:
        Actual wall-clock authoring time for timed human sessions (#428/#429);
        None for untimed AI runs.
    metadata:
        Arbitrary provenance fields (prompt hash, model temperature, retry count…).
    """

    spec_id: str
    entrant_id: str
    entrant_type: EntrantType
    seed: int
    scad_source: Optional[str] = None
    error: Optional[str] = None
    generation_latency_seconds: Optional[float] = None
    time_budget_seconds: Optional[float] = None
    time_to_submit_seconds: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.scad_source is not None and not self.error

    def provenance_key(self) -> str:
        """Stable identifier for this (spec, entrant, seed) triple."""
        return f"{self.spec_id}::{self.entrant_id}::seed={self.seed}"


class VoteOutcome(str, Enum):
    """Outcome of a single blind A/B vote between two entrants."""

    A_WINS = "a_wins"
    B_WINS = "b_wins"
    TIE = "tie"


@dataclass(frozen=True)
class MatchResult:
    """A completed blind-vote comparison between two entrants on the same spec+seed.

    Attributes
    ----------
    spec_id:
        The shared instrument spec.
    seed:
        The shared seed.
    entrant_a_id:
        Left slot entrant.
    entrant_b_id:
        Right slot entrant (blinded during the vote; revealed after).
    outcome:
        Human voter's decision after blind review of rendered outputs.
    voter_id:
        Identifier of the voter (usually ``"tony"``). Single-voter caveat documented
        in epic #421: results are directional, not population claims.
    notes:
        Optional free-text from the voter.
    """

    spec_id: str
    seed: int
    entrant_a_id: str
    entrant_b_id: str
    outcome: VoteOutcome
    voter_id: str = "tony"
    notes: str = ""
