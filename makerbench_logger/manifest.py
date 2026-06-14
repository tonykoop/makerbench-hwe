"""WorkflowManifest data model for the makerbench-logger SDK.

This is the SDK-side definition of the ``workflow_manifest.json`` contract
described in mb#89 (bob's lane). The benchmark's authoritative schema lives in
``makerbench.schema.WorkflowManifest`` once that lands; until then — and in any
standalone install that does not ship the full ``makerbench`` package — this
module provides a dependency-free fallback that emits the *same JSON shape*.

Design rule: zero hard dependencies. Only the standard library is imported so
the SDK is genuinely drop-in beside any agent stack. When ``makerbench.schema``
*is* importable, :func:`validate_with_schema` round-trips the emitted dict
through the authoritative pydantic model so contributors get real validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
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
class Stack:
    """The toolchain that produced the run (mb#89 ``stack``)."""

    orchestrator: Optional[str] = None
    orchestrator_version: Optional[str] = None
    framework: Optional[str] = None
    framework_version: Optional[str] = None
    host_application: Optional[str] = None
    host_application_version: Optional[str] = None
    execution_bridge: Optional[str] = None
    execution_bridge_version: Optional[str] = None


@dataclass
class Metrics:
    """Run accounting (mb#89 ``metrics``)."""

    wall_clock_seconds: Optional[float] = None
    inference_tokens: Optional[int] = None
    tool_calls_count: int = 0
    estimated_cost_usd: Optional[float] = None


@dataclass
class ProvenanceTrace:
    """Disclosed audit pointers (mb#89 ``provenance_trace``)."""

    tool_call_log_url: Optional[str] = None
    session_recording_hash: Optional[str] = None


@dataclass
class ToolCall:
    """One recorded tool-call in the disclosed log."""

    name: str
    iteration: int = 0
    timestamp: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    human_steering: str = HII_L0
    note: Optional[str] = None

    def __post_init__(self) -> None:
        self.human_steering = normalize_hii(self.human_steering)


@dataclass
class HumanInterventionIndex:
    """Aggregate steering disclosure derived from the tool-call log.

    ``overall`` is the maximum steering level observed across the run.
    ``autonomy_ratio`` is the fraction of tool-calls that ran fully autonomously
    (L0): 1.0 means no human touched the loop, 0.0 means every step was steered.
    """

    overall: str = HII_L0
    autonomy_ratio: float = 1.0
    counts: dict[str, int] = field(default_factory=lambda: {HII_L0: 0, HII_L1: 0, HII_L2: 0})

    @classmethod
    def from_calls(cls, calls: list[ToolCall]) -> "HumanInterventionIndex":
        counts = {HII_L0: 0, HII_L1: 0, HII_L2: 0}
        for c in calls:
            counts[c.human_steering] += 1
        total = len(calls)
        autonomous = counts[HII_L0]
        ratio = (autonomous / total) if total else 1.0
        overall = HII_L0
        for level in calls:
            if HII_RANK[level.human_steering] > HII_RANK[overall]:
                overall = level.human_steering
        return cls(overall=overall, autonomy_ratio=round(ratio, 4), counts=counts)


@dataclass
class WorkflowManifest:
    """SDK-side ``workflow_manifest.json`` (mb#89).

    Mirrors the benchmark contract: a hard-graded geometry artifact paired with a
    disclosed process audit log. The geometry is the source of truth; this
    manifest discloses *how* it was produced.
    """

    schema_version: str = SCHEMA_VERSION
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    seed: Optional[int] = None
    created_at: Optional[str] = None
    stack: Stack = field(default_factory=Stack)
    metrics: Metrics = field(default_factory=Metrics)
    human_intervention_index: HumanInterventionIndex = field(default_factory=HumanInterventionIndex)
    provenance_trace: ProvenanceTrace = field(default_factory=ProvenanceTrace)
    tool_call_log: list[ToolCall] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-serializable dict (the canonical wire shape)."""
        return asdict(self)


def validate_with_schema(manifest_dict: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Round-trip a manifest dict through the authoritative pydantic schema if present.

    Returns ``(True, None)`` when ``makerbench.schema.WorkflowManifest`` is
    importable and accepts the dict, ``(False, reason)`` when it rejects it, and
    ``(True, None)`` (best-effort pass) when the authoritative schema is simply
    absent — the SDK is designed to work standalone.
    """
    try:
        from makerbench.schema import WorkflowManifest as _SchemaManifest  # type: ignore
    except Exception:
        return True, None  # standalone: no authoritative schema to check against
    try:
        _SchemaManifest.model_validate(manifest_dict)  # pydantic v2
        return True, None
    except Exception as exc:  # pragma: no cover - exercised only post bob-merge
        return False, str(exc)
