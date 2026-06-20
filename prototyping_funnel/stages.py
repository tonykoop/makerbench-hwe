"""Prototyping-funnel stage model: concept → DFM → quote → prototype → validation.

Each stage records a cost estimate and lead-time, enabling the full funnel to
accumulate both a deflationary cost curve and calendar risk in a single object.

Story coverage: #81 (costing interface), #82 (quote bridge integration), #240.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Stage = Literal["concept", "dfm", "quote", "prototype", "validation"]

#: Canonical ordering — gates must flow forward through this sequence.
STAGE_ORDER: tuple[Stage, ...] = (
    "concept",
    "dfm",
    "quote",
    "prototype",
    "validation",
)

_STAGE_INDEX: dict[str, int] = {s: i for i, s in enumerate(STAGE_ORDER)}


@dataclass(frozen=True)
class StageResult:
    """Outcome of a single funnel stage for one prototyping iteration.

    ``cost_usd`` is the engineering + tooling + material spend attributed to
    this specific stage.  ``lead_time_days`` is calendar time (not business
    days) for the stage deliverable.
    """

    stage: Stage
    passed: bool
    cost_usd: float = 0.0
    lead_time_days: float = 0.0
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cost_usd < 0:
            raise ValueError(f"cost_usd must be non-negative, got {self.cost_usd}")
        if self.lead_time_days < 0:
            raise ValueError(f"lead_time_days must be non-negative, got {self.lead_time_days}")
        if self.stage not in _STAGE_INDEX:
            raise ValueError(f"unknown stage {self.stage!r}; must be one of {STAGE_ORDER}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "cost_usd": self.cost_usd,
            "lead_time_days": self.lead_time_days,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class IterationRecord:
    """One full pass through the funnel, from concept to a terminal stage.

    Stages must be provided in ``STAGE_ORDER`` order; they need not include
    every stage (e.g. an iteration that fails at DFM has only ``concept`` and
    ``dfm`` stages).
    """

    iteration: int
    stages: tuple[StageResult, ...]

    def __post_init__(self) -> None:
        if self.iteration < 1:
            raise ValueError(f"iteration must be >= 1, got {self.iteration}")
        if not self.stages:
            raise ValueError("at least one StageResult is required")
        # Verify stage ordering
        for i, s in enumerate(self.stages[1:], 1):
            prev = self.stages[i - 1]
            if _STAGE_INDEX[s.stage] <= _STAGE_INDEX[prev.stage]:
                raise ValueError(
                    f"stages must be in forward order; {s.stage!r} follows {prev.stage!r}"
                )

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.stages), 6)

    @property
    def total_lead_time_days(self) -> float:
        return round(sum(s.lead_time_days for s in self.stages), 6)

    @property
    def reached_validation(self) -> bool:
        return any(s.stage == "validation" and s.passed for s in self.stages)

    @property
    def terminal_stage(self) -> Stage:
        return self.stages[-1].stage

    def as_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "total_cost_usd": self.total_cost_usd,
            "total_lead_time_days": self.total_lead_time_days,
            "reached_validation": self.reached_validation,
            "terminal_stage": self.terminal_stage,
            "stages": [s.as_dict() for s in self.stages],
        }


class PrototypingFunnel:
    """Accumulates funnel iterations and tracks the deflationary cost curve.

    This is the single-source object for epic #240: each call to
    :meth:`add_iteration` records one design-build-test loop; :meth:`curve`
    returns the deflationary cost series; :meth:`summary` provides sprint KPIs.

    Usage::

        funnel = PrototypingFunnel("acrylic-bracket")
        funnel.add_iteration(IterationRecord(
            iteration=1,
            stages=(
                StageResult("concept", passed=True, cost_usd=150, lead_time_days=2),
                StageResult("dfm",     passed=True, cost_usd=0,   lead_time_days=1),
                StageResult("quote",   passed=True, cost_usd=0,   lead_time_days=1),
                StageResult("prototype", passed=False, cost_usd=220, lead_time_days=7),
            ),
        ))
        funnel.add_iteration(...)  # next cycle, expected to be cheaper
        print(funnel.cost_reduction_fraction())
    """

    def __init__(self, name: str = "prototype") -> None:
        self.name = name
        self._iterations: list[IterationRecord] = []

    def add_iteration(self, record: IterationRecord) -> None:
        """Append an iteration; numbers must be sequential starting at 1."""
        expected = len(self._iterations) + 1
        if record.iteration != expected:
            raise ValueError(
                f"iteration numbers must be sequential; expected {expected}, "
                f"got {record.iteration}"
            )
        self._iterations.append(record)

    @property
    def iterations(self) -> list[IterationRecord]:
        return list(self._iterations)

    # ------------------------------------------------------------------
    # Deflationary-curve helpers
    # ------------------------------------------------------------------

    def curve(self) -> list[dict[str, Any]]:
        """The deflationary cost series — one entry per iteration."""
        return [
            {
                "iteration": r.iteration,
                "total_cost_usd": r.total_cost_usd,
                "total_lead_time_days": r.total_lead_time_days,
                "reached_validation": r.reached_validation,
            }
            for r in self._iterations
        ]

    def cumulative_cost_usd(self) -> float:
        """Total spend across all recorded iterations."""
        return round(sum(r.total_cost_usd for r in self._iterations), 6)

    def cumulative_lead_time_days(self) -> float:
        """Calendar days summed across all recorded iterations."""
        return round(sum(r.total_lead_time_days for r in self._iterations), 6)

    def cost_reduction_fraction(self) -> float | None:
        """Fraction by which per-iteration cost dropped from iteration 1 to the latest.

        Returns ``None`` when fewer than 2 iterations have been recorded.
        A value of 0.30 means cost dropped 30 % from the first iteration.
        """
        if len(self._iterations) < 2:
            return None
        first = self._iterations[0].total_cost_usd
        last = self._iterations[-1].total_cost_usd
        if first == 0:
            return None
        return round(max(0.0, (first - last) / first), 6)

    def lead_time_reduction_fraction(self) -> float | None:
        """Fraction by which lead time dropped from iteration 1 to the latest."""
        if len(self._iterations) < 2:
            return None
        first = self._iterations[0].total_lead_time_days
        last = self._iterations[-1].total_lead_time_days
        if first == 0:
            return None
        return round(max(0.0, (first - last) / first), 6)

    def validation_rate(self) -> float:
        """Fraction of iterations that reached a passing validation stage."""
        if not self._iterations:
            return 0.0
        return round(sum(1 for r in self._iterations if r.reached_validation)
                     / len(self._iterations), 6)

    def summary(self) -> dict[str, Any]:
        """KPI summary suitable for a sprint retrospective or benchmark output."""
        return {
            "name": self.name,
            "iteration_count": len(self._iterations),
            "cumulative_cost_usd": self.cumulative_cost_usd(),
            "cumulative_lead_time_days": self.cumulative_lead_time_days(),
            "cost_reduction_fraction": self.cost_reduction_fraction(),
            "lead_time_reduction_fraction": self.lead_time_reduction_fraction(),
            "validation_rate": self.validation_rate(),
            "curve": self.curve(),
        }
