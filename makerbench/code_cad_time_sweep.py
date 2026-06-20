"""Time-budget sweep analysis for the Code-CAD Arena (#430).

The arena stories compare AI-solo, human-solo, and human+AI entrants under a
fixed authoring budget. This module makes the budget itself a public experiment
axis: callers feed already-scored observations, and the analyzer emits
quality-vs-time curves plus crossover and diminishing-return signals.

The module is deliberately stdlib-only and does not run models, render geometry,
or inspect artifacts. It works only from public aggregate metrics:

* subjective Elo from blind A/B votes
* objective pass-rate from deterministic gates
* entrant type and time budget

Every report carries the single-human-tester caveat explicitly, because a
human-solo or human+AI curve from one tester is an experimental trace, not a
population claim.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

SCHEMA = "makerbench-code-cad-time-sweep-v1"
METRICS = ("subjective_elo", "objective_pass_rate")
SINGLE_HUMAN_TESTER_CONFOUND = (
    "Human and human+AI curves may reflect a single tester's skill, fatigue, and "
    "tool familiarity; do not generalize them as population-level human baselines "
    "without multiple independent testers."
)


@dataclass(frozen=True)
class TimeSweepObservation:
    """One scored entrant result at one budget point."""

    entrant_id: str
    entrant_type: str
    budget_minutes: float
    subjective_elo: float
    objective_pass_rate: float

    def __post_init__(self) -> None:
        if not self.entrant_id:
            raise ValueError("entrant_id is required")
        if not self.entrant_type:
            raise ValueError("entrant_type is required")
        if self.budget_minutes <= 0:
            raise ValueError("budget_minutes must be positive")
        if not (0.0 <= self.objective_pass_rate <= 1.0):
            raise ValueError("objective_pass_rate must be between 0 and 1")


@dataclass(frozen=True)
class TimeCurvePoint:
    """Averaged quality metrics for one entrant type at one budget."""

    entrant_type: str
    budget_minutes: float
    subjective_elo: float
    objective_pass_rate: float
    observation_count: int
    entrant_ids: tuple[str, ...]


@dataclass(frozen=True)
class CrossoverPoint:
    """Where the leading entrant type changes between adjacent budget points."""

    metric: str
    previous_budget_minutes: float
    budget_minutes: float
    leader_before: str
    leader_after: str
    leader_before_value: float
    leader_after_value: float


@dataclass(frozen=True)
class DiminishingReturnPoint:
    """A low marginal-gain segment for one entrant type and metric."""

    metric: str
    entrant_type: str
    start_budget_minutes: float
    end_budget_minutes: float
    absolute_gain: float
    gain_per_minute: float


@dataclass(frozen=True)
class TimeSweepReport:
    """Serializable report for a configurable budget sweep."""

    schema: str
    budgets_minutes: tuple[float, ...]
    entrant_types: tuple[str, ...]
    curves: tuple[TimeCurvePoint, ...]
    crossovers: tuple[CrossoverPoint, ...]
    diminishing_returns: tuple[DiminishingReturnPoint, ...]
    confounds: tuple[str, ...]

    def to_dict(self) -> dict:
        """Return a JSON-ready dictionary with stable ordering."""
        return asdict(self)


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values)


def _round(value: float) -> float:
    return round(value, 6)


def _normalize_filter(values: Optional[Iterable]) -> Optional[set]:
    if values is None:
        return None
    return set(values)


def _curve_points(
    observations: Iterable[TimeSweepObservation],
) -> tuple[TimeCurvePoint, ...]:
    groups: dict[tuple[str, float], list[TimeSweepObservation]] = defaultdict(list)
    for obs in observations:
        groups[(obs.entrant_type, float(obs.budget_minutes))].append(obs)

    points: list[TimeCurvePoint] = []
    for (entrant_type, budget), rows in groups.items():
        points.append(
            TimeCurvePoint(
                entrant_type=entrant_type,
                budget_minutes=_round(budget),
                subjective_elo=_round(_mean(row.subjective_elo for row in rows)),
                objective_pass_rate=_round(_mean(row.objective_pass_rate for row in rows)),
                observation_count=len(rows),
                entrant_ids=tuple(sorted({row.entrant_id for row in rows})),
            )
        )
    return tuple(sorted(points, key=lambda p: (p.budget_minutes, p.entrant_type)))


def _leaders_by_budget(
    curves: Iterable[TimeCurvePoint],
    metric: str,
) -> dict[float, TimeCurvePoint]:
    by_budget: dict[float, list[TimeCurvePoint]] = defaultdict(list)
    for point in curves:
        by_budget[point.budget_minutes].append(point)

    leaders: dict[float, TimeCurvePoint] = {}
    for budget, points in by_budget.items():
        leaders[budget] = max(
            points,
            key=lambda p: (getattr(p, metric), p.entrant_type),
        )
    return leaders


def _crossovers(curves: Iterable[TimeCurvePoint]) -> tuple[CrossoverPoint, ...]:
    curves = tuple(curves)
    found: list[CrossoverPoint] = []
    for metric in METRICS:
        leaders = _leaders_by_budget(curves, metric)
        budgets = sorted(leaders)
        for previous, current in zip(budgets, budgets[1:]):
            before = leaders[previous]
            after = leaders[current]
            if before.entrant_type != after.entrant_type:
                found.append(
                    CrossoverPoint(
                        metric=metric,
                        previous_budget_minutes=previous,
                        budget_minutes=current,
                        leader_before=before.entrant_type,
                        leader_after=after.entrant_type,
                        leader_before_value=_round(getattr(before, metric)),
                        leader_after_value=_round(getattr(after, metric)),
                    )
                )
    return tuple(found)


def _diminishing_returns(
    curves: Iterable[TimeCurvePoint],
    *,
    min_subjective_elo_gain_per_minute: float,
    min_objective_pass_rate_gain_per_minute: float,
) -> tuple[DiminishingReturnPoint, ...]:
    thresholds = {
        "subjective_elo": min_subjective_elo_gain_per_minute,
        "objective_pass_rate": min_objective_pass_rate_gain_per_minute,
    }
    by_type: dict[str, list[TimeCurvePoint]] = defaultdict(list)
    for point in curves:
        by_type[point.entrant_type].append(point)

    found: list[DiminishingReturnPoint] = []
    for entrant_type, points in sorted(by_type.items()):
        points = sorted(points, key=lambda p: p.budget_minutes)
        for before, after in zip(points, points[1:]):
            minutes = after.budget_minutes - before.budget_minutes
            if minutes <= 0:
                continue
            for metric, threshold in thresholds.items():
                gain = getattr(after, metric) - getattr(before, metric)
                gain_per_minute = gain / minutes
                if gain >= 0 and gain_per_minute <= threshold:
                    found.append(
                        DiminishingReturnPoint(
                            metric=metric,
                            entrant_type=entrant_type,
                            start_budget_minutes=before.budget_minutes,
                            end_budget_minutes=after.budget_minutes,
                            absolute_gain=_round(gain),
                            gain_per_minute=_round(gain_per_minute),
                        )
                    )
    return tuple(found)


def analyze_time_budget_sweep(
    observations: Iterable[TimeSweepObservation],
    *,
    budgets_minutes: Optional[Iterable[float]] = None,
    entrant_types: Optional[Iterable[str]] = None,
    min_subjective_elo_gain_per_minute: float = 1.0,
    min_objective_pass_rate_gain_per_minute: float = 0.005,
) -> TimeSweepReport:
    """Build quality-vs-time curves from scored arena observations.

    ``budgets_minutes`` and ``entrant_types`` are optional filters, making one
    analysis run reusable for a full 2/5/10/20 minute sweep or a smaller slice.
    Diminishing returns are flagged when the marginal gain between adjacent
    budget points is non-negative but at or below the configured per-minute
    threshold.
    """
    budget_filter = (
        _normalize_filter(float(v) for v in budgets_minutes)
        if budgets_minutes is not None
        else None
    )
    entrant_filter = _normalize_filter(entrant_types)
    rows = []
    for obs in observations:
        budget = float(obs.budget_minutes)
        if budget_filter is not None and budget not in budget_filter:
            continue
        if entrant_filter is not None and obs.entrant_type not in entrant_filter:
            continue
        rows.append(obs)
    if not rows:
        raise ValueError("at least one observation is required after filtering")

    curves = _curve_points(rows)
    budgets = tuple(sorted({point.budget_minutes for point in curves}))
    types = tuple(sorted({point.entrant_type for point in curves}))
    return TimeSweepReport(
        schema=SCHEMA,
        budgets_minutes=budgets,
        entrant_types=types,
        curves=curves,
        crossovers=_crossovers(curves),
        diminishing_returns=_diminishing_returns(
            curves,
            min_subjective_elo_gain_per_minute=min_subjective_elo_gain_per_minute,
            min_objective_pass_rate_gain_per_minute=min_objective_pass_rate_gain_per_minute,
        ),
        confounds=(SINGLE_HUMAN_TESTER_CONFOUND,),
    )
