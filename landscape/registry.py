"""Adjacent-benchmark registry for MakerBench-HWE (#242).

Loads entries from ``docs/landscape.yaml`` (or an explicitly supplied path) and
provides structured query methods.  Falls back gracefully when PyYAML is absent
(same approach used by ``tests/test_landscape_yaml.py``).

Public surface
--------------
BenchmarkEntry   dataclass holding one landscape.yaml entry in a typed form.
BenchmarkRegistry  collection with query helpers.

The registry intentionally uses **one canonical vocabulary** for domains, task
types, and scoring models — the same vocabulary as the landscape.yaml header —
so callers do not need to know raw field names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Default YAML source (can be overridden in tests)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = _REPO_ROOT / "docs" / "landscape.yaml"

# ---------------------------------------------------------------------------
# Vocabulary — mirrors landscape.yaml header
# ---------------------------------------------------------------------------

AXIS_VOCAB: frozenset[str] = frozenset({
    "spatial-intelligence",
    "hardware-engineering",
    "code-cad",
    "dfm",
    "reverse-engineering",
    "physics-sim",
})

SCORING_VOCAB: frozenset[str] = frozenset({
    "deterministic-geometric",
    "executability",
    "simulation-fea",
    "vlm-judge",
    "llm-judge",
    "human-preference",
    "task-completion",
    "mixed",
})

KIND_VOCAB: frozenset[str] = frozenset({"benchmark", "method", "dataset"})

TYPE_VOCAB: frozenset[str] = frozenset({
    "benchmark",
    "leaderboard",
    "method",
    "method+benchmark",
    "dataset",
    "benchmark+method",
    "method+dataset",
    "product",
})


# ---------------------------------------------------------------------------
# BenchmarkEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BenchmarkEntry:
    """One entry from the landscape registry in a typed, query-friendly form.

    Attributes
    ----------
    name            Human-readable project name (matches landscape.yaml ``name``).
    domain          Axis tags (subset of :data:`AXIS_VOCAB`).
    task_types      Scope tokens + fab_processes tokens, merged into one list.
    scoring_model   Primary grading method string (first token if compound).
    url             Primary source URL.
    kind            ``benchmark`` | ``method`` | ``dataset``.
    entry_type      Full ``type`` field from landscape.yaml.
    recent          True iff tagged *recent* (v1/major revision < 90 days old).
    raw             The full un-typed dict from landscape.yaml.
    """

    name: str
    domain: list[str]
    task_types: list[str]
    scoring_model: str
    url: str
    kind: str
    entry_type: str
    recent: bool = False
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def covers_axis(self, axis: str) -> bool:
        """Return True if this entry covers *axis*."""
        return axis in self.domain

    def is_deterministic(self) -> bool:
        """Return True if the primary scoring model is deterministic."""
        return self.scoring_model in {
            "deterministic-geometric",
            "executability",
            "simulation-fea",
        }

    def is_open(self) -> bool:
        """Return True if the entry is open-source (Apache/MIT/CC/public)."""
        openness = str(self.raw.get("openness", "")).lower()
        return any(kw in openness for kw in (
            "apache", "mit", "cc-by", "gpl", "open", "public", "arxiv",
        ))

    def fab_processes(self) -> list[str]:
        """Return the fab_processes list (empty for methods/datasets)."""
        raw_fp = self.raw.get("fab_processes", [])
        if isinstance(raw_fp, list):
            return raw_fp
        return []

    def agentic_tier(self) -> str:
        """Return the agentic tier string (``none`` if absent)."""
        return str(self.raw.get("agentic", "none"))


# ---------------------------------------------------------------------------
# _normalise helpers
# ---------------------------------------------------------------------------

def _ensure_list(value: Any) -> list[str]:
    """Coerce scalar/list/None → list[str]."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _primary_token(value: Any) -> str:
    """Extract the first vocabulary token from a possibly compound grading string."""
    s = str(value).strip()
    # "n/a (…)" → "n/a"
    if s.lower().startswith("n/a"):
        return "n/a"
    # "deterministic-geometric + numeric" → "deterministic-geometric"
    return s.split()[0].rstrip("+,;")


def _entry_from_dict(raw: dict[str, Any]) -> BenchmarkEntry:
    """Build a BenchmarkEntry from a landscape.yaml raw dict."""
    scope = _ensure_list(raw.get("scope", []))
    fab = _ensure_list(raw.get("fab_processes", []))
    task_types = scope + fab

    return BenchmarkEntry(
        name=str(raw["name"]),
        domain=_ensure_list(raw.get("axis", [])),
        task_types=task_types,
        scoring_model=_primary_token(raw.get("grading", "n/a")),
        url=str(raw.get("source", "")),
        kind=str(raw.get("kind", "benchmark")),
        entry_type=str(raw.get("type", "")),
        recent=bool(raw.get("recent", False)),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# BenchmarkRegistry
# ---------------------------------------------------------------------------

class BenchmarkRegistry:
    """Collection of :class:`BenchmarkEntry` objects with query helpers.

    Parameters
    ----------
    yaml_path
        Path to ``landscape.yaml``.  Defaults to ``docs/landscape.yaml``
        relative to the repo root.  Pass ``None`` only when constructing via
        :meth:`from_entries` (which skips YAML loading entirely).

    Examples
    --------
    >>> reg = BenchmarkRegistry()           # loads docs/landscape.yaml
    >>> len(reg.all()) >= 5
    True
    >>> reg.by_domain("dfm")               # all entries that cover DFM
    [...]
    >>> reg.get("MakerBench-HWE")          # look up by name
    BenchmarkEntry(name='MakerBench-HWE', ...)
    """

    def __init__(self, yaml_path: Path | str | None = DEFAULT_YAML) -> None:
        self._entries: list[BenchmarkEntry] = []
        if yaml_path is not None:
            self._load(Path(yaml_path))

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_entries(cls, entries: Sequence[BenchmarkEntry]) -> "BenchmarkRegistry":
        """Build a registry from an already-parsed list (useful in tests)."""
        reg = cls(yaml_path=None)
        reg._entries = list(entries)
        return reg

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        """Load entries from a landscape.yaml file.

        Silently returns an empty registry if PyYAML is unavailable — callers
        should gate on ``len(registry.all()) > 0`` where needed.
        """
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            return

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return

        if not isinstance(data, dict):
            return

        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            return

        for raw in raw_entries:
            if isinstance(raw, dict) and raw.get("name"):
                self._entries.append(_entry_from_dict(raw))

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def all(self) -> list[BenchmarkEntry]:
        """Return all entries in insertion order."""
        return list(self._entries)

    def benchmarks(self) -> list[BenchmarkEntry]:
        """Return only benchmark-kind entries."""
        return [e for e in self._entries if e.kind == "benchmark"]

    def methods(self) -> list[BenchmarkEntry]:
        """Return only method-kind entries."""
        return [e for e in self._entries if e.kind == "method"]

    def by_domain(self, domain: str) -> list[BenchmarkEntry]:
        """Entries that cover *domain* (an axis vocabulary token)."""
        return [e for e in self._entries if e.covers_axis(domain)]

    def by_scoring_model(self, model: str) -> list[BenchmarkEntry]:
        """Entries whose primary scoring model matches *model*."""
        return [e for e in self._entries if e.scoring_model == model]

    def by_kind(self, kind: str) -> list[BenchmarkEntry]:
        """Entries with exactly the given kind."""
        return [e for e in self._entries if e.kind == kind]

    def recent(self) -> list[BenchmarkEntry]:
        """Entries tagged *recent* (major release within last 90 days)."""
        return [e for e in self._entries if e.recent]

    def get(self, name: str) -> BenchmarkEntry | None:
        """Return the entry with *name*, or ``None``."""
        for e in self._entries:
            if e.name == name:
                return e
        return None

    def search(self, *, domain: str | None = None,
               kind: str | None = None,
               scoring_model: str | None = None,
               recent: bool | None = None) -> list[BenchmarkEntry]:
        """Filter by multiple criteria (all supplied filters must match)."""
        results = self._entries
        if domain is not None:
            results = [e for e in results if e.covers_axis(domain)]
        if kind is not None:
            results = [e for e in results if e.kind == kind]
        if scoring_model is not None:
            results = [e for e in results if e.scoring_model == scoring_model]
        if recent is not None:
            results = [e for e in results if e.recent == recent]
        return results

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:  # pragma: no cover
        return f"BenchmarkRegistry({len(self._entries)} entries)"
