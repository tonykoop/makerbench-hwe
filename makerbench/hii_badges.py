"""Site-facing Human Intervention Index badge metadata.

This module is intentionally stdlib-only so ``site/build_data.py`` can reuse the
same tier rules without importing the full pydantic-backed MakerBench package.
The singular ``makerbench.hii_badge`` module owns certificate-backed SVG badge
rendering; this plural module owns compact payload metadata for leaderboard rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HII_LEVELS = ("L0", "L1", "L2")
HII_LEVEL_RANK = {level: rank for rank, level in enumerate(HII_LEVELS)}


@dataclass(frozen=True)
class HiiBadgeTier:
    """Static metadata for one HII disclosure tier."""

    level: str
    title: str
    label: str
    criteria: str


HII_BADGE_TIERS: dict[str, HiiBadgeTier] = {
    "L0": HiiBadgeTier(
        level="L0",
        title="Pure Autonomy",
        label="L0 Pure Autonomy",
        criteria="Fully autonomous run: no human steering between brief and final artifact.",
    ),
    "L1": HiiBadgeTier(
        level="L1",
        title="Elite Copilot",
        label="L1 Elite Copilot",
        criteria="Light natural-language steering, with no manual geometry editing disclosed.",
    ),
    "L2": HiiBadgeTier(
        level="L2",
        title="Master Triage",
        label="L2 Master Triage",
        criteria="Heavy copilot or manual geometry-edit intervention was disclosed.",
    ),
}


def hii_level_from_value(value: Any) -> str | None:
    """Normalize a WorkflowManifest HII value to ``L0``/``L1``/``L2``.

    Accepts the current schema shape (``{"highest_level": "L1"}``), older public
    row shapes (``"L1"`` or ``{"overall": "L1"}``), and pydantic/dataclass-like
    objects with matching attributes. Unknown or missing values return ``None`` so
    legacy rows remain untouched.
    """
    if value is None:
        return None
    if isinstance(value, str):
        level = value.strip().upper()
        return level if level in HII_BADGE_TIERS else None
    if isinstance(value, dict):
        for key in ("highest_level", "overall", "level"):
            level = hii_level_from_value(value.get(key))
            if level is not None:
                return level
        return None
    for attr in ("highest_level", "overall", "level"):
        level = hii_level_from_value(getattr(value, attr, None))
        if level is not None:
            return level
    return None


def hii_level_from_manifest(manifest: Any) -> str | None:
    """Extract an HII tier from a WorkflowManifest-like object or mapping."""
    if manifest is None:
        return None
    if isinstance(manifest, dict):
        for key in ("hii", "human_intervention_index"):
            level = hii_level_from_value(manifest.get(key))
            if level is not None:
                return level
        return hii_level_from_value(manifest)
    for attr in ("hii", "human_intervention_index"):
        level = hii_level_from_value(getattr(manifest, attr, None))
        if level is not None:
            return level
    return hii_level_from_value(manifest)


def heaviest_hii_level(levels: list[str]) -> str | None:
    """Return the heaviest disclosed tier from normalized HII levels."""
    known = [level for level in levels if level in HII_LEVEL_RANK]
    if not known:
        return None
    return max(known, key=lambda level: HII_LEVEL_RANK[level])


def badge_metadata_for_level(level: str | None) -> dict[str, str] | None:
    """Return JSON-serializable site metadata for an HII tier."""
    if level is None:
        return None
    tier = HII_BADGE_TIERS.get(level)
    if tier is None:
        return None
    return {
        "level": tier.level,
        "title": tier.title,
        "label": tier.label,
        "criteria": tier.criteria,
    }
