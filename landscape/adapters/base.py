"""Base types for the interop-adapter MODEL (#242).

``CommonResult`` is the target format that every stub adapter normalises to.
``BaseInteropAdapter`` is the ABC every concrete adapter implements.

Design constraints
------------------
* **No real API calls.**  Adapters are pure, deterministic transformers of
  already-existing dicts (e.g. the JSON a CADGenBench leaderboard entry
  exports).  They never fetch URLs, call LLMs, or read private oracles.
* **No task generation.**  An adapter only *consumes* an externally owned
  result record; it never constructs a MakerBench task from it.
* **Schema-stable.** ``CommonResult.schema_version`` is bumped when the
  shape of the common format changes, so consumers can pin a version.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# CommonResult — the shared target schema
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "landscape-common-result-v1"


@dataclass
class CommonResult:
    """Normalised result record in the MakerBench-HWE common format.

    Every interop adapter maps its source benchmark's native result dict to
    this structure.

    Attributes
    ----------
    source_benchmark
        Name of the originating benchmark, e.g. ``"CADGenBench"``.
    artifact_id
        Opaque identifier for the artifact / submission (from the source record).
    score
        Primary numeric score in ``[0, 1]`` (or ``None`` if not available).
    dimensions
        Structured sub-scores / metadata the adapter could extract, keyed by
        a short descriptor (e.g. ``"geometric_validity": 0.95``).
    axis_coverage
        Which MakerBench axis tags are relevant to this result.
    grading_transparency
        ``True`` if the source grading is deterministic/reproducible.
    raw
        The original, unmodified input dict.
    schema_version
        Version string for the common format; currently ``"landscape-common-result-v1"``.
    """

    source_benchmark: str
    artifact_id: str
    score: float | None
    dimensions: dict[str, Any] = field(default_factory=dict)
    axis_coverage: list[str] = field(default_factory=list)
    grading_transparency: bool = False
    raw: dict[str, Any] = field(default_factory=dict, compare=False)
    schema_version: str = field(default=_SCHEMA_VERSION, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "schema_version": self.schema_version,
            "source_benchmark": self.source_benchmark,
            "artifact_id": self.artifact_id,
            "score": self.score,
            "dimensions": self.dimensions,
            "axis_coverage": self.axis_coverage,
            "grading_transparency": self.grading_transparency,
        }


# ---------------------------------------------------------------------------
# BaseInteropAdapter
# ---------------------------------------------------------------------------

class BaseInteropAdapter(ABC):
    """Common contract for interop-adapter stubs.

    Subclasses set ``source_benchmark`` and implement :meth:`adapt`.
    ``adapt`` is a **pure function**: same input → same ``CommonResult``.

    Properties guaranteed by every adapter
    ---------------------------------------
    * ``generates_tasks = False``  — adapters consume, never produce, tasks.
    * ``calls_llm = False``        — no LLM/VLM judge inside an adapter.
    * ``calls_oracle = False``     — no private oracle access.
    """

    #: Name of the source benchmark this adapter handles, e.g. ``"CADGenBench"``.
    source_benchmark: str = "unknown"
    #: Human-readable description of what the adapter maps and how.
    description: str = ""
    #: Axis tags the adapted results are relevant to.
    axis_coverage: list[str] = []

    # Invariants — never override these in concrete adapters.
    generates_tasks: bool = False
    calls_llm: bool = False
    calls_oracle: bool = False

    @abstractmethod
    def adapt(self, raw_result: dict[str, Any]) -> CommonResult:
        """Normalise *raw_result* from the source benchmark into a ``CommonResult``.

        Parameters
        ----------
        raw_result
            A single result record as exported by the source benchmark
            (typically a dict parsed from JSON).

        Returns
        -------
        CommonResult
            The normalised record in the common format.

        Raises
        ------
        ValueError
            If *raw_result* is missing fields that the adapter considers
            mandatory (e.g. no artifact identifier at all).
        """

    def describe(self) -> dict[str, Any]:
        """Return adapter metadata for discovery / registration."""
        return {
            "source_benchmark": self.source_benchmark,
            "description": self.description,
            "axis_coverage": list(self.axis_coverage),
            "generates_tasks": self.generates_tasks,
            "calls_llm": self.calls_llm,
            "calls_oracle": self.calls_oracle,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(source={self.source_benchmark!r})"
