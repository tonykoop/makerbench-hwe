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


def step_topology_summary(step_path: str | Path) -> dict[str, Any]:
    """Deterministic OCCT/build123d topology summary of a STEP file.

    The first real B-rep query from docs/BREP_PROFILE.md's roadmap: solid count,
    face count, cylindrical (hole-like) face count, and the bounding-box size in
    mm. Like the rest of this module it is *optional-local* — when build123d is
    not installed it returns an ``unavailable`` status dict instead of raising, so
    public CI exercises the no-dependency path safely.
    """
    availability = build123d_availability()
    if not availability.available:
        return {
            "status": "unavailable",
            "available": False,
            "reason": availability.reason,
            "path": str(step_path),
        }
    try:
        from build123d import import_step
    except Exception as exc:  # pragma: no cover - depends on optional local wheels
        return {
            "status": "error",
            "available": False,
            "reason": f"build123d import failed: {exc}",
            "path": str(step_path),
        }

    try:  # pragma: no cover - exercised only with the optional build123d wheels
        part = import_step(str(step_path))
        faces = list(part.faces())

        def _is_cylinder(face: Any) -> bool:
            return str(getattr(face, "geom_type", "")).upper().endswith("CYLINDER")

        box = part.bounding_box()
        size = getattr(box, "size", None)
        bbox_mm = (
            [round(size.X, 3), round(size.Y, 3), round(size.Z, 3)]
            if size is not None
            else None
        )
        return {
            "status": "summarized",
            "available": True,
            "path": str(step_path),
            "solid_count": len(list(part.solids())),
            "face_count": len(faces),
            "cylindrical_face_count": sum(1 for face in faces if _is_cylinder(face)),
            "bbox_mm": bbox_mm,
        }
    except Exception as exc:  # pragma: no cover - depends on optional local wheels
        return {
            "status": "error",
            "available": True,
            "reason": f"STEP topology read failed: {exc}",
            "path": str(step_path),
        }


def grade_topology(
    summary: dict[str, Any],
    expected: dict[str, Any],
    *,
    bbox_tol_mm: float = 0.5,
) -> dict[str, Any]:
    """Pure proof-of-life grade: compare a topology summary to expectations.

    Deliberately dependency-free so CI can test the grading logic without the
    optional build123d wheels. Only the keys present in ``expected`` are checked
    (``solid_count``, ``cylindrical_face_count``, ``face_count``, ``bbox_mm``),
    so a task can grade just the topology it cares about. This is a separate
    optional-local signal and does NOT touch ``GradeResult.compute_score`` or the
    core L1-L4 leaderboard.
    """
    checks: dict[str, Any] = {}

    for key in ("solid_count", "cylindrical_face_count", "face_count"):
        if key in expected:
            observed = summary.get(key)
            checks[key] = {
                "expected": expected[key],
                "observed": observed,
                "ok": observed == expected[key],
            }

    if "bbox_mm" in expected:
        exp = expected["bbox_mm"]
        obs = summary.get("bbox_mm")
        ok = (
            isinstance(obs, (list, tuple))
            and len(obs) == 3
            and all(abs(o - e) <= bbox_tol_mm for o, e in zip(obs, exp))
        )
        checks["bbox_mm"] = {
            "expected": exp,
            "observed": obs,
            "tol_mm": bbox_tol_mm,
            "ok": bool(ok),
        }

    n_total = len(checks)
    n_pass = sum(1 for check in checks.values() if check["ok"])
    return {
        "checks": checks,
        "n_pass": n_pass,
        "n_total": n_total,
        "passed": n_total > 0 and n_pass == n_total,
    }


def grade_brep_smoke(
    step_path: str | Path,
    expected: dict[str, Any],
    *,
    bbox_tol_mm: float = 0.5,
) -> dict[str, Any]:
    """End-to-end optional-local proof-of-life grade for a STEP artifact.

    Reads the STEP topology (optional-local) and grades it against ``expected``.
    When build123d is unavailable it returns a ``skipped`` status so public CI
    stays green; locally it returns the per-check grade plus the raw summary.
    """
    summary = step_topology_summary(step_path)
    if not summary.get("available"):
        return {
            "status": "skipped",
            "available": False,
            "reason": summary.get("reason"),
            "profile": "brep-build123d",
            "path": str(step_path),
        }
    grade = grade_topology(summary, expected, bbox_tol_mm=bbox_tol_mm)
    return {
        "status": "graded",
        "available": True,
        "profile": "brep-build123d",
        "summary": summary,
        **grade,
    }
