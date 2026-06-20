"""Batch interop sweeper for MakerBench-HWE (#242).

Runs multiple interop adapters over a batch of result dicts and aggregates the
outputs into a ``SweepResult``.  This is the "fan-out" layer: a benchmark
maintainer or CI job can call :func:`sweep` with a mixed list of
``(adapter_name, result_dict)`` pairs and get back a flat list of
:class:`~landscape.adapters.base.CommonResult` objects plus per-benchmark stats.

No network calls, no oracle access, no LLM invocations — see adapter invariants.

Public surface
--------------
SweepEntry      A single (adapter_name, raw_result) input pair.
SweepResult     The output: list of CommonResults + aggregate stats.
sweep           Convenience function: run a batch sweep in one call.
BatchSweeper    Stateful class for repeated sweeps (caches adapter instances).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .adapters import adapter_registry, CommonResult
from .adapters.base import BaseInteropAdapter


# ---------------------------------------------------------------------------
# SweepEntry
# ---------------------------------------------------------------------------

@dataclass
class SweepEntry:
    """A single input record for a batch sweep.

    Attributes
    ----------
    adapter_name    Key into :data:`~landscape.adapters.adapter_registry`,
                    e.g. ``"CADGenBench"``.
    raw_result      The raw result dict from the source benchmark.
    label           Optional human-readable label for this entry (e.g. run ID).
    """
    adapter_name: str
    raw_result: dict[str, Any]
    label: str = ""


# ---------------------------------------------------------------------------
# SweepResult
# ---------------------------------------------------------------------------

@dataclass
class SweepResult:
    """Output of a batch sweep.

    Attributes
    ----------
    results         All successfully adapted :class:`CommonResult` objects.
    errors          Per-entry error messages keyed by entry index.
    stats           Per-source-benchmark statistics.
    """

    results: list[CommonResult] = field(default_factory=list)
    errors: dict[int, str] = field(default_factory=dict)
    stats: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def by_benchmark(self, name: str) -> list[CommonResult]:
        """Return all CommonResults from *name*."""
        return [r for r in self.results if r.source_benchmark == name]

    def deterministic_only(self) -> list[CommonResult]:
        """Return only results where grading_transparency is True."""
        return [r for r in self.results if r.grading_transparency]

    def nondeterministic_only(self) -> list[CommonResult]:
        """Return only results where grading_transparency is False (e.g. VLM-judged)."""
        return [r for r in self.results if not r.grading_transparency]

    def scores(self) -> list[float]:
        """Return all non-None score values."""
        return [r.score for r in self.results if r.score is not None]

    def avg_score(self) -> float | None:
        """Return the mean score across all results, or None if no scores."""
        s = self.scores()
        return sum(s) / len(s) if s else None

    def success_count(self) -> int:
        """Return the number of successfully adapted results."""
        return len(self.results)

    def error_count(self) -> int:
        """Return the number of failed adaptations."""
        return len(self.errors)

    def to_dict(self) -> dict[str, Any]:
        """Serialise summary (excluding raw result dicts) to a plain dict."""
        return {
            "success_count": self.success_count(),
            "error_count": self.error_count(),
            "avg_score": self.avg_score(),
            "stats": self.stats,
            "results": [r.to_dict() for r in self.results],
            "errors": {str(k): v for k, v in self.errors.items()},
        }


# ---------------------------------------------------------------------------
# BatchSweeper
# ---------------------------------------------------------------------------

class BatchSweeper:
    """Stateful batch sweeper that caches adapter instances.

    Parameters
    ----------
    registry
        Mapping of adapter name → adapter class.  Defaults to the built-in
        :data:`~landscape.adapters.adapter_registry`.
    skip_unknown
        If True, silently skip entries with an unknown adapter name instead of
        recording an error.

    Examples
    --------
    >>> sweeper = BatchSweeper()
    >>> entries = [
    ...     SweepEntry("CADGenBench", {"task_id": "t1", "overall_score": 0.8}),
    ...     SweepEntry("BenDFM", {"entry_id": "p1", "checks": {}}),
    ... ]
    >>> result = sweeper.run(entries)
    >>> result.success_count()
    2
    """

    def __init__(self,
                 registry: dict[str, type[BaseInteropAdapter]] | None = None,
                 skip_unknown: bool = False) -> None:
        self._registry = registry if registry is not None else adapter_registry
        self._skip_unknown = skip_unknown
        self._cache: dict[str, BaseInteropAdapter] = {}

    def _get_adapter(self, name: str) -> BaseInteropAdapter | None:
        if name not in self._cache:
            cls = self._registry.get(name)
            if cls is None:
                return None
            self._cache[name] = cls()
        return self._cache[name]

    def run(self, entries: list[SweepEntry]) -> SweepResult:
        """Run the batch sweep.

        Parameters
        ----------
        entries     List of :class:`SweepEntry` input records.

        Returns
        -------
        SweepResult
            Aggregated output with per-benchmark stats.
        """
        results: list[CommonResult] = []
        errors: dict[int, str] = {}

        for idx, entry in enumerate(entries):
            adapter = self._get_adapter(entry.adapter_name)
            if adapter is None:
                if not self._skip_unknown:
                    errors[idx] = (
                        f"No adapter registered for {entry.adapter_name!r}"
                    )
                continue
            try:
                cr = adapter.adapt(entry.raw_result)
                results.append(cr)
            except Exception as exc:
                errors[idx] = f"{type(exc).__name__}: {exc}"

        stats = _compute_stats(results)
        return SweepResult(results=results, errors=errors, stats=stats)


def _compute_stats(results: list[CommonResult]) -> dict[str, dict[str, Any]]:
    """Compute per-benchmark statistics from a list of CommonResults."""
    by_bench: dict[str, list[CommonResult]] = {}
    for r in results:
        by_bench.setdefault(r.source_benchmark, []).append(r)

    stats: dict[str, dict[str, Any]] = {}
    for bench, rs in by_bench.items():
        scores = [r.score for r in rs if r.score is not None]
        stats[bench] = {
            "count": len(rs),
            "score_count": len(scores),
            "avg_score": sum(scores) / len(scores) if scores else None,
            "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None,
            "transparent_count": sum(1 for r in rs if r.grading_transparency),
        }
    return stats


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def sweep(entries: list[SweepEntry],
          skip_unknown: bool = False) -> SweepResult:
    """Run a one-shot batch sweep.

    Parameters
    ----------
    entries         List of :class:`SweepEntry` input records.
    skip_unknown    Silently skip unknown adapter names if True.

    Returns
    -------
    SweepResult
    """
    sweeper = BatchSweeper(skip_unknown=skip_unknown)
    return sweeper.run(entries)
