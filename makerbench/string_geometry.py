"""Advisory string-instrument geometry check (#981, epic #978).

**Advisory only.** The result is labelled ``advisory`` and never changes a sub-score, the
objective pass rate, pass/fail, the blind series or Elo. It rides along under
``objective["advisory"]["strings"]`` and is reported per tier in ``advisory_report.json``.

Modelled specs
    ``family == "strings"``. Every other spec reports ``not modelled``.

String detection (works on a unioned mesh)
    OpenSCAD unions every top-level object, so a string usually arrives fused to its
    anchors rather than as a separate body. Strings are found by local thickness instead:
    every face casts one ray inward along its normal (the shape-diameter of the part under
    it, deterministic, no sampling). Faces thinner than ``max_diameter_mm`` are grouped by
    edge adjacency, and a group is a **string** when its oriented bounding box is long
    (``>= MIN_STRING_LENGTH_MM``), slender (``length / width >= MIN_ASPECT``) and narrow
    (``width <= 1.5 * max_diameter_mm``). A thin soundboard is thin too, but wide, so it is
    not a string. A string's length is its bounding-box length (anchor to anchor).

Checks (each failure is explained in the #903 shape)
    ``string_count``
        Detected strings vs ``string_count`` (or an integer ``strings``). Declared
        ``sympathetic_string_count`` strings may also be present.
    ``string_length``
        Shortest and longest detected strings vs the declared speaking lengths:
        ``string_length_range_mm``, ``shortest/longest_speaking_length_mm``,
        ``speaking_length_min/max_mm``, or a single ``scale_length_mm`` /
        ``speaking_length_mm`` (then the median string). A modelled string often runs past
        its speaking length to its anchors, so the window is ``-LENGTH_TOL_BELOW`` to
        ``+LENGTH_TOL_ABOVE`` of the declared value.
    ``string_clearance``
        Points on each string's axis between 10 % and 90 % of its length (anchors excluded)
        are measured against the rest of the assembly. A string whose clearance is below
        ``MIN_CLEARANCE_MM`` over more than ``MAX_LOW_CLEARANCE_FRACTION`` of those points
        lies on or in the soundboard or body.

Not modelled
    String tension, gauge, bridge break angle, fret placement and action. A string buried
    entirely inside material has no surface left and shows up as a missing string.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import trimesh

from . import measure

LABEL = "advisory"
NOT_MODELLED = "not modelled"
FAMILY = "string_geometry"
DEFAULT_MAX_DIAMETER_MM = 4.0
MIN_STRING_LENGTH_MM = 40.0
MIN_ASPECT = 15.0
LENGTH_TOL_BELOW = 0.15
LENGTH_TOL_ABOVE = 0.30
MIN_CLEARANCE_MM = 1.0
MAX_LOW_CLEARANCE_FRACTION = 0.2
AXIS_SAMPLES = 17


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def modelled_reason(spec: Mapping[str, Any]) -> str | None:
    if spec.get("family") != "strings":
        return f"family {spec.get('family')!r} is not a string instrument"
    return None


def declared_count(constraints: Mapping[str, Any]) -> tuple[int | None, int]:
    """``(main strings, allowed sympathetic strings)``."""
    count = _int(constraints.get("string_count")) or _int(constraints.get("strings"))
    return count, _int(constraints.get("sympathetic_string_count")) or 0


def declared_lengths(constraints: Mapping[str, Any]) -> dict[str, Any] | None:
    """The declared speaking-length window: ``{"shortest", "longest"}`` or ``{"scale"}``."""
    span = constraints.get("string_length_range_mm")
    if isinstance(span, (list, tuple)) and len(span) == 2 and all(_num(v) for v in span):
        return {"shortest": float(min(span)), "longest": float(max(span)), "source": "string_length_range_mm"}
    for lo_key, hi_key in (("shortest_speaking_length_mm", "longest_speaking_length_mm"),
                           ("speaking_length_min_mm", "speaking_length_max_mm")):
        lo, hi = _num(constraints.get(lo_key)), _num(constraints.get(hi_key))
        if lo and hi:
            return {"shortest": min(lo, hi), "longest": max(lo, hi), "source": f"{lo_key}/{hi_key}"}
    for key in ("scale_length_mm", "speaking_length_mm"):
        scale = _num(constraints.get(key))
        if scale:
            return {"scale": scale, "source": key}
    return None


def shape_diameter(mesh: trimesh.Trimesh) -> np.ndarray:
    """Per-face thickness: distance from the face centre to the first surface hit inward."""
    centers = mesh.triangles_center
    normals = mesh.face_normals
    origins = centers - normals * 1e-3
    _, ray_ids, locations = mesh.ray.intersects_id(origins, -normals, multiple_hits=False,
                                                   return_locations=True)
    sdf = np.full(len(centers), np.inf)
    sdf[ray_ids] = np.linalg.norm(locations - origins[ray_ids], axis=1)
    return sdf


def detect_strings(mesh: trimesh.Trimesh, *, max_diameter_mm: float = DEFAULT_MAX_DIAMETER_MM) -> dict:
    """Long, slender, thin face groups of a mesh, longest first."""
    sdf = shape_diameter(mesh)
    thin = sdf <= max_diameter_mm
    adjacency = mesh.face_adjacency
    keep = thin[adjacency[:, 0]] & thin[adjacency[:, 1]]
    groups = trimesh.graph.connected_components(adjacency[keep], nodes=np.nonzero(thin)[0], min_len=1)
    strings = []
    for faces in groups:
        points = mesh.triangles[faces].reshape(-1, 3)
        if len(points) < 9:
            continue
        to_obb, extents = trimesh.bounds.oriented_bounds(points)
        order = np.argsort(extents)[::-1]
        length, width = float(extents[order[0]]), float(extents[order[1]])
        if length < MIN_STRING_LENGTH_MM or width > 1.5 * max_diameter_mm or length / max(width, 1e-6) < MIN_ASPECT:
            continue
        from_obb = np.linalg.inv(to_obb)
        axis = from_obb[:3, order[0]]
        center = from_obb[:3, 3]
        diameter = float(np.median(sdf[faces]))
        strings.append({"faces": np.asarray(faces), "length_mm": length, "width_mm": width,
                        "diameter_mm": diameter, "axis": axis / np.linalg.norm(axis), "center": center})
    strings.sort(key=lambda s: -s["length_mm"])
    return {"strings": strings, "thin_faces": int(thin.sum()), "faces": len(mesh.faces)}


def _clearances(mesh: trimesh.Trimesh, strings: list[dict]) -> list[dict]:
    string_faces = np.concatenate([s["faces"] for s in strings]) if strings else np.array([], dtype=int)
    rest_faces = np.setdiff1d(np.arange(len(mesh.faces)), string_faces)
    if len(rest_faces) == 0:
        return [{"min_clearance_mm": None, "low_fraction": 0.0} for _ in strings]
    rest = mesh.submesh([rest_faces], append=True)
    out = []
    for s in strings:
        ts = np.linspace(-0.4, 0.4, AXIS_SAMPLES) * s["length_mm"]
        points = s["center"] + ts[:, None] * s["axis"]
        _, distance, _ = trimesh.proximity.closest_point(rest, points)
        clearance = distance - s["diameter_mm"] / 2.0
        out.append({"min_clearance_mm": float(clearance.min()),
                    "low_fraction": float(np.mean(clearance < MIN_CLEARANCE_MM))})
    return out


def _explain(check: str, *, measured, threshold, unit: str, requires: str, body_id: str | None,
             detail: str, **extra: Any) -> dict[str, Any]:
    return {"check": check, "measured": measured, "threshold": threshold, "unit": unit,
            "requires": requires, "body_id": body_id, "detail": detail, **extra}


def advise(spec: Mapping[str, Any], mesh: trimesh.Trimesh) -> dict[str, Any]:
    """The labelled string-geometry advisory for one compiled candidate. Never affects scoring."""
    base: dict[str, Any] = {"label": LABEL, "affects_scoring": False}
    reason = modelled_reason(spec)
    if reason:
        return {**base, "status": NOT_MODELLED, "reason": reason}
    problem = measure.mesh_problem(mesh)
    if problem:
        return {**base, "status": "not measurable", "family": FAMILY, "error": problem, "failures": []}
    constraints = spec.get("constraints") or {}
    max_diameter = _num(constraints.get("string_max_diameter_mm")) or DEFAULT_MAX_DIAMETER_MM
    found = detect_strings(mesh, max_diameter_mm=max_diameter)
    strings = found["strings"]
    clearances = _clearances(mesh, strings)
    failures: list[dict] = []

    count, sympathetic = declared_count(constraints)
    if count is not None and not count <= len(strings) <= count + sympathetic:
        allowed = f"{count}" if not sympathetic else f"{count}-{count + sympathetic}"
        failures.append(_explain(
            "string_count", measured=len(strings), threshold=count, unit="strings",
            requires=f"measured in [{allowed}]" + (" (sympathetic strings allowed)" if sympathetic else ""),
            body_id="assembly",
            detail=f"{len(strings)} string bodies detected (long, slender parts thinner than "
                   f"{max_diameter:g} mm), {allowed} declared"
                   + ("; strings fused into a thicker part or omitted are not detected" if len(strings) < count else "")))

    lengths = [s["length_mm"] for s in strings]
    window = declared_lengths(constraints)
    length_checks = []
    if window and lengths:
        if "scale" in window:
            length_checks.append(("median string", float(np.median(lengths)), window["scale"]))
        else:
            length_checks.append(("shortest string", min(lengths), window["shortest"]))
            length_checks.append(("longest string", max(lengths), window["longest"]))
    for which, measured, declared in length_checks:
        lo, hi = declared * (1 - LENGTH_TOL_BELOW), declared * (1 + LENGTH_TOL_ABOVE)
        if not lo <= measured <= hi:
            failures.append(_explain(
                "string_length", measured=round(measured, 1), threshold=declared, unit="mm",
                requires=f"{lo:.0f} <= measured <= {hi:.0f} (declared -{LENGTH_TOL_BELOW:.0%}/+{LENGTH_TOL_ABOVE:.0%})",
                body_id="assembly", which=which,
                detail=f"{which} is {measured:.0f} mm anchor to anchor; {window['source']} declares "
                       f"{declared:g} mm ({measured / declared - 1:+.0%})"))

    per_string = []
    for index, (s, c) in enumerate(zip(strings, clearances)):
        body = f"string_{index}"
        per_string.append({"body_id": body, "length_mm": round(s["length_mm"], 1),
                           "diameter_mm": round(s["diameter_mm"], 2),
                           "min_clearance_mm": None if c["min_clearance_mm"] is None else round(c["min_clearance_mm"], 2),
                           "low_clearance_fraction": round(c["low_fraction"], 3)})
        if c["low_fraction"] > MAX_LOW_CLEARANCE_FRACTION:
            failures.append(_explain(
                "string_clearance", measured=round(c["low_fraction"], 3), threshold=MAX_LOW_CLEARANCE_FRACTION,
                unit=f"fraction of the string's span with clearance < {MIN_CLEARANCE_MM:g} mm",
                requires="measured <= threshold", body_id=body,
                detail=f"{body} ({s['length_mm']:.0f} mm) comes within {MIN_CLEARANCE_MM:g} mm of the rest of the "
                       f"assembly over {c['low_fraction']:.0%} of its span (closest "
                       f"{c['min_clearance_mm']:+.2f} mm): it lies on or in the soundboard or body"))

    return {
        **base,
        "status": "inconsistent" if failures else "consistent",
        "family": FAMILY,
        "failures": failures,
        "detected": len(strings),
        "declared": {"string_count": count, "sympathetic_string_count": sympathetic or None,
                     "lengths": window},
        "strings": per_string,
        "measured": {"thin_faces": found["thin_faces"], "faces": found["faces"],
                     "max_diameter_mm": max_diameter,
                     "length_range_mm": [round(min(lengths), 1), round(max(lengths), 1)] if lengths else None},
        "method": "shape-diameter thin faces grouped by adjacency; long slender groups are strings",
        "assumptions": [
            f"a string is thinner than {max_diameter:g} mm, at least {MIN_STRING_LENGTH_MM:g} mm long and "
            f"{MIN_ASPECT:g}x longer than wide",
            "string length = bounding-box length (anchor to anchor), not the exact speaking length",
            f"clearance measured on the middle 80% of each string; {MIN_CLEARANCE_MM:g} mm minimum",
            "not modelled: tension, gauge, break angle, frets, action",
        ],
    }

