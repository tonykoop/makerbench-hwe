"""Optional build123d / OCCT B-rep profile helpers.

This module must stay importable without build123d or OCCT installed. The public
CI path uses it to prove optional-heavy B-rep support is gated; local users can
install build123d and run the smoke exporter for a STEP proof-of-life artifact.
"""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrepAvailability:
    """Availability result for the optional build123d/OCCT stack."""

    available: bool
    status: str
    reason: str = ""


def build123d_availability() -> BrepAvailability:
    """Return whether the optional build123d dependency is importable."""
    if importlib.util.find_spec("build123d") is None:
        return BrepAvailability(
            available=False,
            status="unavailable",
            reason="build123d is not installed; B-rep/STEP checks are optional_local",
        )
    return BrepAvailability(available=True, status="available")


def export_smoke_step(output_path: str | Path) -> dict[str, Any]:
    """Export a tiny build123d STEP smoke artifact when optional deps exist.

    Returns a machine-readable status dictionary instead of raising for missing
    build123d so public CI can exercise the no-dependency path safely.
    """
    availability = build123d_availability()
    if not availability.available:
        return {
            "status": "skipped",
            "available": False,
            "reason": availability.reason,
            "path": str(output_path),
        }

    try:
        from build123d import Box, BuildPart, Cylinder, Mode, export_step
    except Exception as exc:  # pragma: no cover - depends on optional local wheels
        return {
            "status": "error",
            "available": False,
            "reason": f"build123d import failed: {exc}",
            "path": str(output_path),
        }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with BuildPart() as plate:
            Box(40.0, 20.0, 4.0)
            Cylinder(radius=3.0, height=8.0, mode=Mode.SUBTRACT)
        export_step(plate.part, str(path))
    except Exception as exc:  # pragma: no cover - depends on optional local wheels
        return {
            "status": "error",
            "available": True,
            "reason": f"STEP smoke export failed: {exc}",
            "path": str(path),
        }

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "status": "exported",
        "available": True,
        "path": str(path),
        "sha256": digest,
        "bytes": path.stat().st_size,
    }
