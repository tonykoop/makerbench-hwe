"""WorkflowManifest data model for the makerbench-logger SDK.

This is the SDK-side definition of the ``workflow_manifest.json`` contract
described in mb#89. The benchmark's authoritative schema lives in
``makerbench.schema.WorkflowManifest``; this module is a dependency-free local
mirror that emits the **same JSON shape** so the SDK is genuinely drop-in beside
any agent stack.

Design rule: zero hard dependencies. Only the standard library is imported. When
``makerbench.schema`` *is* importable, :func:`validate_with_schema` round-trips
the emitted dict through the authoritative pydantic model so contributors get
real validation.

Wire-shape contract (mb#89, must match ``makerbench/schema.py``):

* top-level keys ``schema_version``, ``task_id``, ``seed``, ``stack``,
  ``metrics``, ``hii``, ``provenance_trace`` (plus optional disclosure extras the
  authoritative model ignores: ``run_id``, ``created_at``, ``tool_call_log``,
  ``artifacts``, ``notes``);
* ``hii`` is the :class:`HumanInterventionIndex` with the event-count fields
  ``l0_autonomous_events`` / ``l1_nl_steering_events`` / ``l2_copilot_manual_events``,
  a weighted ``autonomy_ratio`` (L0=1.0, L1=0.5, L2=0.0), and ``highest_level``.

The previous SDK release emitted a ``human_intervention_index`` field with a
different shape; the authoritative pydantic model silently *dropped* it and
recorded the run as fully autonomous (L0), erasing any disclosed human steering.
This module emits the conforming ``hii`` shape so steering disclosure survives
the round-trip — an integrity requirement for the workflow league.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = "0.1"

# --- Human Intervention Index (mb#89) ---------------------------------------
# L0 fully autonomous; L1 light NL steering between iterations;
# L2 heavy copilot / manual geometry edits.
HII_L0 = "L0"
HII_L1 = "L1"
HII_L2 = "L2"
HII_LEVELS = (HII_L0, HII_L1, HII_L2)
HII_RANK = {HII_L0: 0, HII_L1: 1, HII_L2: 2}

# Autonomy weights used to derive the ratio. MUST match
# makerbench.schema.HumanInterventionIndex.from_events: an L0 event contributes
# full autonomy (1.0), an L1 half (0.5), an L2 none (0.0).
HII_WEIGHT = {HII_L0: 1.0, HII_L1: 0.5, HII_L2: 0.0}


def normalize_hii(level: Any) -> str:
    """Coerce an int (0/1/2) or string (``L0``/``l1``/``2``) to a canonical HII level."""
    if isinstance(level, bool):  # guard: bool is an int subclass
        level = int(level)
    if isinstance(level, int):
        if level not in (0, 1, 2):
            raise ValueError(f"HII level int must be 0, 1, or 2; got {level!r}")
        return HII_LEVELS[level]
    s = str(level).strip().upper()
    if s in HII_RANK:
        return s
    if s in ("0", "1", "2"):
        return HII_LEVELS[int(s)]
    raise ValueError(f"Unrecognized HII level {level!r}; expected L0/L1/L2 or 0/1/2")


@dataclass
class ComponentVersion:
    """A named stack component and the version that produced a run (mb#89)."""

    name: str
    version: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}


def _component(value: Any) -> Optional[dict[str, Any]]:
    """Normalize a stack slot to the authoritative ``{name, version}`` object.

    Accepts ``None``, a plain string (logger-era shorthand), a ``(name, version)``
    pair, a :class:`ComponentVersion`, or a dict. Returns ``None`` for an empty
    slot so absent components serialize as ``null`` like the authoritative model.
    """
    if value is None:
        return None
    if isinstance(value, ComponentVersion):
        return value.to_dict()
    if isinstance(value, str):
        return {"name": value, "version": None}
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return {"name": value[0], "version": value[1]}
    if isinstance(value, dict):
        return {"name": value.get("name"), "version": value.get("version")}
    raise TypeError(f"cannot coerce {value!r} to a stack ComponentVersion")


@dataclass
class Stack:
    """The agentic CAD stack that produced the run (mb#89 ``stack``).

    Each slot may be a plain string (``"blender-mcp"``), a ``(name, version)``
    pair, or a :class:`ComponentVersion`; all serialize to the authoritative
    ``{"name": ..., "version": ...}`` object (or ``null`` when unset).
    """

    orchestrator: Any = None
    framework: Any = None
    host_application: Any = None
    execution_bridge: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "orchestrator": _component(self.orchestrator),
            "framework": _component(self.framework),
            "host_application": _component(self.host_application),
            "execution_bridge": _component(self.execution_bridge),
        }


@dataclass
class Metrics:
    """Run accounting (mb#89 ``metrics``)."""

    wall_clock_seconds: Optional[float] = None
    inference_tokens: Optional[int] = None
    tool_calls_count: Optional[int] = None
    estimated_cost_usd: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "wall_clock_seconds": self.wall_clock_seconds,
            "inference_tokens": self.inference_tokens,
            "tool_calls_count": self.tool_calls_count,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass
class ProvenanceTrace:
    """Disclosed audit pointers (mb#89 ``provenance_trace``)."""

    tool_call_log_url: Optional[str] = None
    session_recording_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_log_url": self.tool_call_log_url,
            "session_recording_hash": self.session_recording_hash,
        }


@dataclass
class ToolCall:
    """One recorded tool-call in the disclosed log (SDK-side disclosure extra)."""

    name: str
    iteration: int = 0
    timestamp: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    human_steering: str = HII_L0
    note: Optional[str] = None

    def __post_init__(self) -> None:
        self.human_steering = normalize_hii(self.human_steering)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "params": self.params,
            "human_steering": self.human_steering,
            "note": self.note,
        }


@dataclass
class HumanInterventionIndex:
    """Steering disclosure (mb#89 ``hii``) — the authoritative event-count shape.

    Counts interventions per tier and derives a weighted ``autonomy_ratio``
    (L0=1.0, L1=0.5, L2=0.0) and the ``highest_level`` observed. This mirrors
    ``makerbench.schema.HumanInterventionIndex`` so the emitted ``hii`` block
    round-trips losslessly through the authoritative model.
    """

    l0_autonomous_events: int = 0
    l1_nl_steering_events: int = 0
    l2_copilot_manual_events: int = 0
    autonomy_ratio: float = 1.0
    highest_level: str = HII_L0

    @classmethod
    def from_events(cls, *, l0: int = 0, l1: int = 0, l2: int = 0) -> "HumanInterventionIndex":
        l0, l1, l2 = max(0, l0), max(0, l1), max(0, l2)
        total = l0 + l1 + l2
        if total == 0:
            ratio = 1.0
        else:
            ratio = (
                l0 * HII_WEIGHT[HII_L0]
                + l1 * HII_WEIGHT[HII_L1]
                + l2 * HII_WEIGHT[HII_L2]
            ) / total
        if l2 > 0:
            highest = HII_L2
        elif l1 > 0:
            highest = HII_L1
        else:
            highest = HII_L0
        return cls(
            l0_autonomous_events=l0,
            l1_nl_steering_events=l1,
            l2_copilot_manual_events=l2,
            autonomy_ratio=round(ratio, 6),
            highest_level=highest,
        )

    @classmethod
    def from_calls(cls, calls: list[ToolCall]) -> "HumanInterventionIndex":
        counts = {HII_L0: 0, HII_L1: 0, HII_L2: 0}
        for c in calls:
            counts[c.human_steering] += 1
        return cls.from_events(
            l0=counts[HII_L0], l1=counts[HII_L1], l2=counts[HII_L2]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "l0_autonomous_events": self.l0_autonomous_events,
            "l1_nl_steering_events": self.l1_nl_steering_events,
            "l2_copilot_manual_events": self.l2_copilot_manual_events,
            "autonomy_ratio": self.autonomy_ratio,
            "highest_level": self.highest_level,
        }


@dataclass
class WorkflowManifest:
    """SDK-side ``workflow_manifest.json`` (mb#89).

    Mirrors the benchmark contract: a hard-graded geometry artifact paired with a
    disclosed process audit log. The geometry is the source of truth; this
    manifest discloses *how* it was produced. ``run_id`` / ``created_at`` /
    ``tool_call_log`` / ``artifacts`` / ``notes`` are SDK disclosure extras the
    authoritative model ignores; ``hii`` carries the canonical steering summary.
    """

    schema_version: str = SCHEMA_VERSION
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    seed: Optional[int] = None
    created_at: Optional[str] = None
    stack: Stack = field(default_factory=Stack)
    metrics: Metrics = field(default_factory=Metrics)
    hii: HumanInterventionIndex = field(default_factory=HumanInterventionIndex)
    provenance_trace: ProvenanceTrace = field(default_factory=ProvenanceTrace)
    tool_call_log: list[ToolCall] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-serializable dict (the canonical mb#89 wire shape)."""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "seed": self.seed,
            "stack": self.stack.to_dict(),
            "metrics": self.metrics.to_dict(),
            "hii": self.hii.to_dict(),
            "provenance_trace": self.provenance_trace.to_dict(),
            # SDK disclosure extras (ignored by the authoritative model):
            "run_id": self.run_id,
            "created_at": self.created_at,
            "tool_call_log": [c.to_dict() for c in self.tool_call_log],
            "artifacts": list(self.artifacts),
            "notes": list(self.notes),
        }


def validate_with_schema(manifest_dict: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Round-trip a manifest dict through the authoritative pydantic schema if present.

    Returns ``(True, None)`` when ``makerbench.schema.WorkflowManifest`` is
    importable and accepts the dict, ``(False, reason)`` when it rejects it, and
    ``(True, None)`` (best-effort pass) when the authoritative schema is simply
    absent — the SDK is designed to work standalone.

    Beyond pydantic acceptance this also guards against the silent-drop failure
    mode: it confirms the parsed ``hii`` round-trips back to the emitted event
    counts, so a manifest that *parsed* but lost its steering disclosure is
    reported as invalid rather than passing as fully autonomous.
    """
    try:
        from makerbench.schema import WorkflowManifest as _SchemaManifest  # type: ignore
    except Exception:
        return True, None  # standalone: no authoritative schema to check against
    try:
        parsed = _SchemaManifest.model_validate(manifest_dict)
    except Exception as exc:
        return False, str(exc)
    # Integrity guard: the disclosed steering must survive the round-trip.
    emitted_hii = manifest_dict.get("hii")
    if isinstance(emitted_hii, dict):
        for key in (
            "l0_autonomous_events",
            "l1_nl_steering_events",
            "l2_copilot_manual_events",
        ):
            if getattr(parsed.hii, key) != emitted_hii.get(key, 0):
                return (
                    False,
                    "hii steering disclosure was not preserved through the "
                    f"authoritative schema (field {key!r}); emitted shape is "
                    "non-conforming",
                )
    return True, None
