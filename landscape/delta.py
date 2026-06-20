"""Landscape delta (diff) utility for MakerBench-HWE (#242).

Compares two :class:`~landscape.registry.BenchmarkRegistry` snapshots — e.g. the
registry from last quarter's sweep vs. the current one — and reports what
changed: new entries, removed entries, and per-field diffs for unchanged names.

Useful for quarterly sweep review automation: import both the previous evidence
YAML and the current landscape.yaml, diff them, and get a structured changeset.

Public surface
--------------
FieldChange     One field that changed between two snapshots.
EntryDelta      Per-entry change record (new / removed / modified).
LandscapeDelta  The full diff between two registry snapshots.
diff_registries Convenience function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import BenchmarkRegistry, BenchmarkEntry


# ---------------------------------------------------------------------------
# FieldChange
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldChange:
    """A single field that changed between two registry snapshots.

    Attributes
    ----------
    field_name  The name of the field that changed.
    old_value   Value in the *before* snapshot.
    new_value   Value in the *after* snapshot.
    """
    field_name: str
    old_value: Any
    new_value: Any

    def is_meaningful(self) -> bool:
        """True if the change is non-trivial (not just whitespace)."""
        return str(self.old_value).strip() != str(self.new_value).strip()


# ---------------------------------------------------------------------------
# EntryDelta
# ---------------------------------------------------------------------------

@dataclass
class EntryDelta:
    """Change record for one benchmark entry.

    Attributes
    ----------
    name        The benchmark name.
    status      One of ``"added"``, ``"removed"``, ``"modified"``, ``"unchanged"``.
    changes     List of :class:`FieldChange` objects (empty unless ``"modified"``).
    """

    name: str
    status: str  # "added" | "removed" | "modified" | "unchanged"
    changes: list[FieldChange] = field(default_factory=list)

    def is_added(self) -> bool:
        return self.status == "added"

    def is_removed(self) -> bool:
        return self.status == "removed"

    def is_modified(self) -> bool:
        return self.status == "modified"

    def is_unchanged(self) -> bool:
        return self.status == "unchanged"

    def changed_fields(self) -> list[str]:
        """Return list of field names that changed."""
        return [c.field_name for c in self.changes]


# ---------------------------------------------------------------------------
# LandscapeDelta
# ---------------------------------------------------------------------------

@dataclass
class LandscapeDelta:
    """Full diff between two registry snapshots.

    Attributes
    ----------
    before_count    Number of entries in the *before* snapshot.
    after_count     Number of entries in the *after* snapshot.
    deltas          Per-entry change records.
    """

    before_count: int = 0
    after_count: int = 0
    deltas: list[EntryDelta] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def added(self) -> list[EntryDelta]:
        """Entries present in *after* but not *before*."""
        return [d for d in self.deltas if d.is_added()]

    def removed(self) -> list[EntryDelta]:
        """Entries present in *before* but not *after*."""
        return [d for d in self.deltas if d.is_removed()]

    def modified(self) -> list[EntryDelta]:
        """Entries present in both snapshots but with changed fields."""
        return [d for d in self.deltas if d.is_modified()]

    def unchanged(self) -> list[EntryDelta]:
        """Entries present in both snapshots with no field changes."""
        return [d for d in self.deltas if d.is_unchanged()]

    def net_change(self) -> int:
        """Number of added entries minus number of removed entries."""
        return len(self.added()) - len(self.removed())

    def has_changes(self) -> bool:
        """True if any entries were added, removed, or modified."""
        return bool(self.added() or self.removed() or self.modified())

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """One-line human-readable summary."""
        return (
            f"+{len(self.added())} added, "
            f"-{len(self.removed())} removed, "
            f"~{len(self.modified())} modified, "
            f"={len(self.unchanged())} unchanged "
            f"(before={self.before_count}, after={self.after_count})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "before_count": self.before_count,
            "after_count": self.after_count,
            "summary": self.summary(),
            "added": [d.name for d in self.added()],
            "removed": [d.name for d in self.removed()],
            "modified": [
                {
                    "name": d.name,
                    "changed_fields": d.changed_fields(),
                    "changes": [
                        {
                            "field": c.field_name,
                            "old": str(c.old_value)[:200],
                            "new": str(c.new_value)[:200],
                        }
                        for c in d.changes
                    ],
                }
                for d in self.modified()
            ],
            "unchanged_count": len(self.unchanged()),
        }

    def to_text(self) -> str:
        """Render a human-readable diff."""
        lines = [
            "Landscape delta:",
            f"  {self.summary()}",
            "",
        ]
        for d in self.added():
            lines.append(f"  + {d.name}")
        for d in self.removed():
            lines.append(f"  - {d.name}")
        for d in self.modified():
            lines.append(f"  ~ {d.name}: {', '.join(d.changed_fields())}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# _COMPARABLE_FIELDS — fields we diff between two entries
# ---------------------------------------------------------------------------

_COMPARABLE_FIELDS = [
    ("scoring_model", "grading"),
    ("kind", "kind"),
    ("entry_type", "type"),
    ("recent", "recent"),
    ("url", "url"),
]

# Additional raw fields to compare (from raw dict)
_RAW_COMPARE_FIELDS = [
    "openness",
    "agentic",
    "anti_memorization",
    "dataset_scale",
]


def _entry_field_changes(before: BenchmarkEntry,
                         after: BenchmarkEntry) -> list[FieldChange]:
    """Return list of changed fields between *before* and *after*."""
    changes: list[FieldChange] = []

    # Typed dataclass fields
    for attr, label in _COMPARABLE_FIELDS:
        old = getattr(before, attr)
        new = getattr(after, attr)
        if old != new:
            changes.append(FieldChange(label, old, new))

    # Domain (axis) list comparison
    if sorted(before.domain) != sorted(after.domain):
        changes.append(FieldChange("axis", sorted(before.domain), sorted(after.domain)))

    # Raw dict field comparison
    for raw_field in _RAW_COMPARE_FIELDS:
        old_val = before.raw.get(raw_field, "")
        new_val = after.raw.get(raw_field, "")
        if str(old_val).strip() != str(new_val).strip():
            changes.append(FieldChange(raw_field, old_val, new_val))

    return changes


# ---------------------------------------------------------------------------
# diff_registries
# ---------------------------------------------------------------------------

def diff_registries(before: BenchmarkRegistry,
                    after: BenchmarkRegistry) -> LandscapeDelta:
    """Compute the diff between two registry snapshots.

    Parameters
    ----------
    before  The *before* snapshot (e.g. from the previous sweep).
    after   The *after* snapshot (e.g. the current landscape.yaml).

    Returns
    -------
    LandscapeDelta
    """
    before_map = {e.name: e for e in before.all()}
    after_map = {e.name: e for e in after.all()}

    all_names = sorted(set(before_map) | set(after_map))
    deltas: list[EntryDelta] = []

    for name in all_names:
        in_before = name in before_map
        in_after = name in after_map

        if in_after and not in_before:
            deltas.append(EntryDelta(name=name, status="added"))
        elif in_before and not in_after:
            deltas.append(EntryDelta(name=name, status="removed"))
        else:
            changes = _entry_field_changes(before_map[name], after_map[name])
            status = "modified" if changes else "unchanged"
            deltas.append(EntryDelta(name=name, status=status, changes=changes))

    return LandscapeDelta(
        before_count=len(before_map),
        after_count=len(after_map),
        deltas=deltas,
    )
