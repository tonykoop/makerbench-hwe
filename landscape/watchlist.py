"""Volatile watchlist helper for MakerBench-HWE (#242).

The ``sweep.volatile_watchlist`` in ``docs/landscape.yaml`` names entries that
are actively changing (upcoming releases, grading model in flux, leaderboard
just launched, etc.).  This module surfaces the watchlist as a typed query
interface so that CI jobs, sprint planning, and gap-finder reports can
reference it without parsing the YAML directly.

Public surface
--------------
WatchlistEntry      One entry on the volatile watchlist (name + registry info).
VolatileWatchlist   Container for the watchlist with query helpers.
load_watchlist      Load the watchlist from a landscape.yaml path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import BenchmarkRegistry, BenchmarkEntry, DEFAULT_YAML


# ---------------------------------------------------------------------------
# WatchlistEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchlistEntry:
    """One entry on the volatile watchlist.

    Attributes
    ----------
    name            The entry name (matches landscape.yaml ``name``).
    registry_entry  The corresponding :class:`BenchmarkEntry`, if present.
                    ``None`` if the watchlist names an entry not yet in the
                    registry (this can happen between sweeps when an entry is
                    anticipated but not yet filed).
    """

    name: str
    registry_entry: BenchmarkEntry | None = field(default=None, compare=False)

    def is_recent(self) -> bool:
        """True if the registry entry is tagged *recent*."""
        return self.registry_entry.recent if self.registry_entry else False

    def axis(self) -> list[str]:
        """Axis tags from the registry entry, or [] if not yet filed."""
        return list(self.registry_entry.domain) if self.registry_entry else []

    def scoring_model(self) -> str:
        """Scoring model from the registry entry, or 'unknown'."""
        return self.registry_entry.scoring_model if self.registry_entry else "unknown"

    def url(self) -> str:
        """Source URL from the registry entry, or ''."""
        return self.registry_entry.url if self.registry_entry else ""


# ---------------------------------------------------------------------------
# VolatileWatchlist
# ---------------------------------------------------------------------------

class VolatileWatchlist:
    """Volatile-watchlist container with query helpers.

    Parameters
    ----------
    names       Raw watchlist names from landscape.yaml.
    registry    :class:`BenchmarkRegistry` to cross-reference.

    Examples
    --------
    >>> wl = load_watchlist()
    >>> len(wl) >= 6
    True
    >>> "MUSE" in wl.names()
    True
    >>> recent = wl.recent_entries()
    """

    def __init__(self, names: list[str], registry: BenchmarkRegistry) -> None:
        self._names = list(names)
        self._registry = registry
        self._entries: list[WatchlistEntry] = [
            WatchlistEntry(
                name=n,
                registry_entry=registry.get(n),
            )
            for n in names
        ]

    def names(self) -> list[str]:
        """Return the raw list of watchlist names."""
        return list(self._names)

    def all(self) -> list[WatchlistEntry]:
        """Return all watchlist entries."""
        return list(self._entries)

    def recent_entries(self) -> list[WatchlistEntry]:
        """Return entries that are currently tagged *recent*."""
        return [e for e in self._entries if e.is_recent()]

    def unfiled_entries(self) -> list[WatchlistEntry]:
        """Return entries that are on the watchlist but not yet in the registry."""
        return [e for e in self._entries if e.registry_entry is None]

    def filed_entries(self) -> list[WatchlistEntry]:
        """Return entries that are on the watchlist and have a registry entry."""
        return [e for e in self._entries if e.registry_entry is not None]

    def by_axis(self, axis: str) -> list[WatchlistEntry]:
        """Return watchlist entries covering *axis*."""
        return [e for e in self._entries if axis in e.axis()]

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._names

    def __repr__(self) -> str:  # pragma: no cover
        return f"VolatileWatchlist({len(self._entries)} entries)"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "count": len(self._entries),
            "recent_count": len(self.recent_entries()),
            "unfiled_count": len(self.unfiled_entries()),
            "entries": [
                {
                    "name": e.name,
                    "recent": e.is_recent(),
                    "axis": e.axis(),
                    "scoring_model": e.scoring_model(),
                    "url": e.url(),
                    "filed": e.registry_entry is not None,
                }
                for e in self._entries
            ],
        }

    def summary(self) -> str:
        """Return a human-readable summary."""
        lines = [
            f"Volatile watchlist ({len(self._entries)} entries):",
            f"  Recent (90-day):  {len(self.recent_entries())}",
            f"  Unfiled entries:  {len(self.unfiled_entries())}",
            "",
        ]
        for e in self._entries:
            flags = []
            if e.is_recent():
                flags.append("RECENT")
            if e.registry_entry is None:
                flags.append("UNFILED")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {e.name}{flag_str}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# load_watchlist
# ---------------------------------------------------------------------------

def load_watchlist(yaml_path: Path | str | None = None,
                   registry: BenchmarkRegistry | None = None) -> VolatileWatchlist:
    """Load the volatile watchlist from ``docs/landscape.yaml``.

    Parameters
    ----------
    yaml_path
        Path to landscape.yaml.  Defaults to ``docs/landscape.yaml``.
    registry
        Pre-loaded registry.  If not provided, one is built from *yaml_path*.

    Returns
    -------
    VolatileWatchlist
        Populated watchlist.  Returns an empty watchlist if PyYAML is not
        installed or the file cannot be read.
    """
    path = Path(yaml_path) if yaml_path else DEFAULT_YAML
    reg = registry if registry is not None else BenchmarkRegistry(yaml_path=path)

    try:
        import yaml  # type: ignore[import]
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return VolatileWatchlist([], reg)
        names = data.get("sweep", {}).get("volatile_watchlist", [])
        if not isinstance(names, list):
            names = []
    except (ImportError, FileNotFoundError, Exception):
        names = []

    return VolatileWatchlist(names=names, registry=reg)
