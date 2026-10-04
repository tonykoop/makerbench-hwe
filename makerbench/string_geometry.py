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
    edge adjacency, and a group is a free stretch of string when its oriented bounding box
    is long (``>= MIN_STRING_LENGTH_MM``), slender (``length / width >= MIN_ASPECT``) and
    narrow (``width <= 1.5 * max_diameter_mm``). A thin soundboard is thin too, but wide.
    A string fused to a nut and a bridge is cut into several stretches, which are joined end
    to end: straight on (parallel within 2 degrees, on one axis within a string diameter), or
    bent up to 20 degrees across a short gap inside a shared nut or bridge (a run-out at a
    break angle). Stretches that overlap along their axis are side by side (a doubled course)
    and never join. Strings are counted, not stretches. Run-outs bent beyond 20 degrees (up to
    60) still join their string but are reported as ``unsupported_runouts``. Thin faces on
    flat patches wider than a string (a narrow saddle's sides) are dropped before grouping.

Contacts, supports and speaking length (``string_profile``)
    Each string's whole path, anchor to anchor, is sampled. A point is in contact when no
    free stretch covers it (fused into something) or its clearance to the rest of the
    assembly is below ``MIN_CLEARANCE_MM``. The outermost contact at each end of the path
    terminates the string: an anchor (fused into it) or a support (a nut or bridge it runs
    over). Short contacts between them are supports only when the spec declares intermediate
    bridges; everything else is a contact fault. The speaking length is the longest free
    interval between neighbouring supports.

Checks (each failure is explained in the #903 shape)
    ``string_count``
        Detected strings vs ``string_count`` (or an integer ``strings``). Declared
        ``sympathetic_string_count`` strings may also be present.
    ``string_length``
        Every string's speaking length vs the declared window (``-LENGTH_TOL_BELOW`` to
        ``+LENGTH_TOL_ABOVE``): a single ``scale_length_mm`` / ``speaking_length_mm`` /
        ``vibrating_string_length_mm``, or a range (``string_length_range_mm``,
        ``shortest/longest_speaking_length_mm``, ``speaking_length_min/max_mm``,
        ``min/max_speaking_length_mm``), whose shortest and longest ends must also be met.
    ``string_clearance``
        Any contact fault on a string's path: it touches or lies on the soundboard or body
        away from its supports.

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
COLLINEAR_COS = float(np.cos(np.radians(2.0)))
#: Run-outs (#995 review): a stretch leaving a nut or bridge at a break angle up to this many
#: degrees, across a gap up to ``JOIN_MAX_MM`` inside that support, is the same string.
RUNOUT_MAX_DEG = 20.0
RUNOUT_MAX_COS = float(np.cos(np.radians(RUNOUT_MAX_DEG)))
#: Run-outs bent further than ``RUNOUT_MAX_DEG`` (a steep headstock, 21-25 degrees) are still
#: joined to their string up to this angle, so they are never counted as extra strings, but are
#: reported as ``unsupported_runouts``: outside the range this check is calibrated for.
RUNOUT_LINK_MAX_DEG = 60.0
RUNOUT_LINK_MAX_COS = float(np.cos(np.radians(RUNOUT_LINK_MAX_DEG)))
#: A short contact between two near-collinear stretches whose directions differ by at least this
#: much (but under the 2 degree collinear limit) may be a bridge with a shallow afterlength: an
#: ``ambiguous_termination``, not a contact fault.
BREAK_MIN_DEG = 0.5
JOIN_MAX_MM = 25.0
OVERLAP_TOL_MM = 1.0
SAME_ROD_FRAC = 0.75
SUPPORT_MAX_MM, SUPPORT_MAX_FRAC = 25.0, 0.05
ANCHOR_MAX_MM, ANCHOR_MAX_FRAC = 40.0, 0.10
COARSE_STEP_MM, MAX_COARSE_SAMPLES, FINE_STEP_MM = 4.0, 400, 0.5


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
                           ("speaking_length_min_mm", "speaking_length_max_mm"),
                           ("min_speaking_length_mm", "max_speaking_length_mm")):
        lo, hi = _num(constraints.get(lo_key)), _num(constraints.get(hi_key))
        if lo and hi:
            return {"shortest": min(lo, hi), "longest": max(lo, hi), "source": f"{lo_key}/{hi_key}"}
    for key in ("scale_length_mm", "speaking_length_mm", "vibrating_string_length_mm"):
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


def _plate_faces(mesh: trimesh.Trimesh, sdf: np.ndarray, max_diameter_mm: float) -> np.ndarray:
    """Thin faces that lie on a flat patch wider than a string can be (#995 review): the sides of
    a narrow saddle, nut or plate. A string's flat facets are strips no wider than its diameter,
    so these faces are dropped before grouping, and a saddle fused under every string no longer
    joins them all into one wide group (which was then discarded: 0 strings)."""
    plate = np.zeros(len(mesh.faces), dtype=bool)
    thin = sdf <= max_diameter_mm
    for facet, normal in zip(mesh.facets, mesh.facets_normal):
        if not thin[facet].any():
            continue
        points = mesh.vertices[np.unique(mesh.faces[facet])]
        u = np.cross(normal, [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(normal, u)
        flat = np.c_[points @ u, points @ v]
        if len(flat) < 3:
            continue
        try:
            _, extents = trimesh.bounds.oriented_bounds_2D(flat)
        except Exception:  # noqa: BLE001 - a degenerate facet is not a plate
            continue
        if float(min(extents)) > max_diameter_mm:
            plate[facet] = True
    return plate


def _segments(mesh: trimesh.Trimesh, sdf: np.ndarray, max_diameter_mm: float) -> list[dict]:
    """Long, slender, thin face groups: one per free stretch of a string."""
    thin = (sdf <= max_diameter_mm) & ~_plate_faces(mesh, sdf, max_diameter_mm)
    adjacency = mesh.face_adjacency
    keep = thin[adjacency[:, 0]] & thin[adjacency[:, 1]]
    groups = trimesh.graph.connected_components(adjacency[keep], nodes=np.nonzero(thin)[0], min_len=1)
    segments = []
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
        segments.append({"faces": np.asarray(faces), "points": points, "length_mm": length,
                         "width_mm": width, "diameter_mm": float(np.median(sdf[faces])),
                         "axis": axis / np.linalg.norm(axis), "center": from_obb[:3, 3]})
    return segments


def _ends(seg: Mapping[str, Any]) -> list[tuple[np.ndarray, np.ndarray]]:
    """The segment's two ends along its axis, each with its outward direction."""
    t = (seg["points"] - seg["center"]) @ seg["axis"]
    return [(seg["center"] + float(t.min()) * seg["axis"], -seg["axis"]),
            (seg["center"] + float(t.max()) * seg["axis"], seg["axis"])]


def _link_cost(mesh: trimesh.Trimesh, a: Mapping[str, Any], end_a: tuple, b: Mapping[str, Any],
               end_b: tuple) -> float | None:
    """How well end ``end_a`` of segment ``a`` continues into end ``end_b`` of ``b`` as one string
    (#995 review), or ``None`` when it cannot. The two ends must face each other with no
    longitudinal overlap, so parallel strings side by side (a doubled course) never join. A
    straight continuation (parallel within 2 degrees, the other end on this axis within a string
    diameter) may cross any fused stretch. A bend (a run-out leaving the nut or bridge at a
    break angle up to ``RUNOUT_MAX_DEG``) joins only across a short gap inside material: the two
    stretches share that nut or bridge."""
    (pa, da), (pb, db) = end_a, end_b
    if float(np.dot(da, db)) >= 0.0:
        return None  # not facing each other
    gap = pb - pa
    if float(np.dot(gap, da)) < -OVERLAP_TOL_MM or float(np.dot(-gap, db)) < -OVERLAP_TOL_MM:
        return None  # overlapping along the axis: side by side, not end to end
    cos = abs(float(np.dot(a["axis"], b["axis"])))
    if cos < RUNOUT_LINK_MAX_COS:
        return None
    off_a = float(np.linalg.norm(gap - float(np.dot(gap, a["axis"])) * a["axis"]))
    off_b = float(np.linalg.norm(gap - float(np.dot(gap, b["axis"])) * b["axis"]))
    tolerance = max(a["diameter_mm"], b["diameter_mm"], 1.0)
    distance = float(np.linalg.norm(gap))
    if cos >= COLLINEAR_COS:
        if max(off_a, off_b) > tolerance:
            return None
    else:
        sine = float(np.sqrt(max(0.0, 1.0 - cos * cos)))
        if distance > JOIN_MAX_MM or max(off_a, off_b) > tolerance + distance * sine:
            return None
        middle = (pa + pb) / 2.0
        if float(trimesh.proximity.signed_distance(mesh, middle[None, :])[0]) < -0.05:
            return None  # the bend is in the air, not on a shared support
    return off_a + off_b + 1e-3 * distance


def _same_rod(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Two stretches that overlap along one axis are partial surfaces of ONE rod (a fused
    contact strips one side of a string) when their axes are closer than ``SAME_ROD_FRAC`` of a
    string diameter. Two separate strings are at least a diameter apart centre to centre, so a
    doubled course never qualifies (#995 review)."""
    if abs(float(np.dot(a["axis"], b["axis"]))) < COLLINEAR_COS:
        return False
    ta = (a["points"] - a["center"]) @ a["axis"]
    tb = (b["points"] - a["center"]) @ a["axis"]
    if min(ta.max(), tb.max()) - max(ta.min(), tb.min()) <= OVERLAP_TOL_MM:
        return False
    limit = SAME_ROD_FRAC * max(a["diameter_mm"], b["diameter_mm"])
    for one, two in ((a, b), (b, a)):
        offset = two["center"] - one["center"]
        if float(np.linalg.norm(offset - float(np.dot(offset, one["axis"])) * one["axis"])) > limit:
            return False
    return True


def _link_segments(mesh: trimesh.Trimesh, segments: list[dict]) -> list[tuple[int, int]]:
    """Segment pairs of one string: partial surfaces of the same rod, and end-to-end
    continuations with each segment end used at most once (best first), so a run-out joins its
    own string, never a neighbour's in the same course."""
    candidates = []
    links = []
    ends = [_ends(seg) for seg in segments]
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            if _same_rod(segments[i], segments[j]):
                links.append((i, j))
                continue
            for ei in range(2):
                for ej in range(2):
                    cost = _link_cost(mesh, segments[i], ends[i][ei], segments[j], ends[j][ej])
                    if cost is not None:
                        candidates.append((cost, i, ei, j, ej))
    used: set[tuple[int, int]] = set()
    for _, i, ei, j, ej in sorted(candidates):
        if (i, ei) in used or (j, ej) in used:
            continue
        used |= {(i, ei), (j, ej)}
        links.append((i, j))
    return links


def detect_strings(mesh: trimesh.Trimesh, *, max_diameter_mm: float = DEFAULT_MAX_DIAMETER_MM) -> dict:
    """Strings of a mesh, longest first (#981).

    Thin face groups are the free stretches of a string; on a unioned mesh a string that
    runs over a nut and a bridge (or touches anything) is cut into several stretches, which
    are joined end to end into one string (straight on, or bent over a shared nut or bridge;
    see :func:`_link_cost`). Each string's path is its straight part through its longest
    stretch, with its axis, its extent along the axis (``t0``..``t1``, anchor to anchor) and the
    intervals its free stretches cover; run-outs bent away beyond a support belong to the
    string (counted once) but not to its path.
    """
    sdf = shape_diameter(mesh)
    segments = _segments(mesh, sdf, max_diameter_mm)
    parent = list(range(len(segments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in _link_segments(mesh, segments):
        parent[find(i)] = find(j)
    chains: dict[int, list[dict]] = {}
    for i, seg in enumerate(segments):
        chains.setdefault(find(i), []).append(seg)
    strings = []
    for chain in chains.values():
        longest = max(chain, key=lambda m: m["length_mm"])
        axis = longest["axis"]
        tolerance = max(longest["diameter_mm"], 1.0)

        def straight(m: Mapping[str, Any], longest=longest, axis=axis, tolerance=tolerance) -> bool:
            offset = m["center"] - longest["center"]
            across = offset - float(np.dot(offset, axis)) * axis
            return (abs(float(np.dot(m["axis"], axis))) >= COLLINEAR_COS
                    and float(np.linalg.norm(across)) <= tolerance)

        members = [m for m in chain if straight(m)]
        points = np.vstack([m["points"] for m in members])
        # the axis line runs through the middle of the cross-section's bounding box (a partly
        # fused string keeps only part of its surface, so the point mean is biased)
        mean = points.mean(axis=0)
        across = points - mean - np.outer((points - mean) @ axis, axis)
        u = np.cross(axis, [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        pu, pv = across @ u, across @ v
        center = mean + u * (pu.min() + pu.max()) / 2.0 + v * (pv.min() + pv.max()) / 2.0
        t = (points - center) @ axis
        t0, t1 = float(t.min()), float(t.max())
        stretches = sorted((float(((m["points"] - center) @ axis).min()),
                            float(((m["points"] - center) @ axis).max()), m["axis"]) for m in members)
        free = [(a, b) for a, b, _ in stretches]
        runout_angles = sorted(round(float(np.degrees(np.arccos(min(1.0, abs(float(np.dot(m["axis"], axis))))))), 1)
                               for m in chain if not any(m is k for k in members))
        strings.append({"faces": np.concatenate([m["faces"] for m in chain]), "segments": len(chain),
                        "runouts": len(chain) - len(members), "runout_angles_deg": runout_angles,
                        "unsupported_runouts": sum(a > RUNOUT_MAX_DEG for a in runout_angles),
                        "length_mm": t1 - t0, "width_mm": max(m["width_mm"] for m in members),
                        "diameter_mm": float(np.median([m["diameter_mm"] for m in members])),
                        "axis": axis, "center": center, "t0": t0, "t1": t1, "free": free,
                        "free_axes": [ax for _, _, ax in stretches]})
    strings.sort(key=lambda st: -st["length_mm"])
    return {"strings": strings, "segments": len(segments), "thin_faces": int((sdf <= max_diameter_mm).sum()),
            "faces": len(mesh.faces)}


def _runs(ts: np.ndarray, contact: np.ndarray) -> list[tuple[float, float]]:
    runs, start = [], None
    for k, flag in enumerate(contact):
        if flag and start is None:
            start = k
        if not flag and start is not None:
            runs.append((float(ts[start]), float(ts[k - 1])))
            start = None
    if start is not None:
        runs.append((float(ts[start]), float(ts[-1])))
    return runs


def string_profile(rest: trimesh.Trimesh | None, string: Mapping[str, Any], *,
                   intermediate_bridges: bool = False) -> dict[str, Any]:
    """Contacts along one string's whole path, its supports and its speaking length (#981).

    The path from anchor to anchor is sampled (coarse, then refined wherever the coarse
    clearance is low). A point is in **contact** when no free stretch of the string covers
    it (the string is fused into something there) or its clearance to the rest of the
    assembly is below ``MIN_CLEARANCE_MM``. Contact runs are then classified:

    * an **anchor**: touches an end of the path and is at most ``max(ANCHOR_MAX_MM,
      ANCHOR_MAX_FRAC * length)`` long;
    * a **support** (nut, bridge, saddle): short (``max(SUPPORT_MAX_MM, SUPPORT_MAX_FRAC *
      length)``), not at an end, and the outermost contact on its side of the path (#995
      review: a string runs over its nut or bridge and on to a free or bent-away run-out);
    * with declared intermediate bridges (a guzheng's moveable bridges), a short contact
      between the terminating ones is an ``intermediate_bridge``;
    * anything else is a **contact fault**: the string touches or lies on the body between
      its terminating supports.

    The speaking length is the longest free interval between neighbouring supports or
    anchors (the path ends count as anchors).
    """
    t0, t1 = string["t0"], string["t1"]
    total = t1 - t0
    radius = string["diameter_mm"] / 2.0

    def covered(ts: np.ndarray) -> np.ndarray:
        inside = np.zeros(len(ts), dtype=bool)
        for a, b in string["free"]:
            inside |= (ts >= a - 1e-6) & (ts <= b + 1e-6)
        return inside

    def clearance(ts: np.ndarray) -> np.ndarray:
        if rest is None or len(ts) == 0:
            return np.full(len(ts), np.inf)
        points = string["center"] + ts[:, None] * string["axis"]
        _, distance, _ = trimesh.proximity.closest_point(rest, points)
        return distance - radius

    coarse_step = max(COARSE_STEP_MM, total / MAX_COARSE_SAMPLES)
    edges = [v for ab in string["free"] for v in ab]
    free = string["free"]
    gap_mids = [(a_end + b_start) / 2.0 for (_, a_end), (b_start, _) in zip(free, free[1:]) if b_start > a_end]
    ts = np.unique(np.concatenate([np.arange(t0, t1, coarse_step), [t1], edges, gap_mids]))
    ts = ts[(ts >= t0) & (ts <= t1)]
    clear = np.full(len(ts), np.inf)
    inside = covered(ts)
    clear[inside] = clearance(ts[inside])
    # refine around every coarse sample that could hide a contact
    suspicious = ts[inside & (clear < MIN_CLEARANCE_MM + coarse_step)]
    if len(suspicious):
        fine = np.unique(np.concatenate([np.arange(t - coarse_step, t + coarse_step, FINE_STEP_MM)
                                         for t in suspicious]))
        fine = fine[(fine >= t0) & (fine <= t1)]
        fine_inside = covered(fine)
        fine_clear = np.full(len(fine), np.inf)
        fine_clear[fine_inside] = clearance(fine[fine_inside])
        ts = np.concatenate([ts, fine])
        clear = np.concatenate([clear, fine_clear])
        inside = np.concatenate([inside, fine_inside])
        order = np.argsort(ts, kind="stable")
        ts, clear, inside = ts[order], clear[order], inside[order]
    # gaps between free stretches are contacts even between samples
    contact = ~inside | (clear < MIN_CLEARANCE_MM)
    runs = _runs(ts, contact)

    anchor_max = max(ANCHOR_MAX_MM, ANCHOR_MAX_FRAC * total)
    support_max = max(SUPPORT_MAX_MM, SUPPORT_MAX_FRAC * total)

    def terminating(a: float, b: float) -> str | None:
        """``anchor`` or ``support`` when this run can end the string's speaking part."""
        if (a - t0 <= coarse_step or t1 - b <= coarse_step) and b - a <= anchor_max:
            return "anchor"
        if not (a - t0 <= coarse_step or t1 - b <= coarse_step) and b - a <= support_max:
            return "support"
        return None

    # #995 review: the string is terminated by the OUTERMOST contact at each end of its path
    # (the anchor it is fused into, or the nut or bridge it runs over before a free end).
    # Every other contact lies between them, on the speaking part: a fault, unless the spec
    # declares intermediate bridges and the contact is short. A support past an anchor with a
    # clear break angle ends the straight path itself (detect_strings); one with a shallow bend
    # (BREAK_MIN_DEG to 2 degrees) is an ambiguous termination, reported and never failed.
    ends = {}
    if runs and terminating(*runs[0]):
        ends[0] = terminating(*runs[0])
    if len(runs) > 1 and terminating(*runs[-1]):
        ends[len(runs) - 1] = terminating(*runs[-1])
    def break_angle(a: float, b: float) -> float | None:
        """Direction change (degrees) between the free stretches either side of ``a..b``."""
        axes, free = string.get("free_axes") or [], string["free"]
        if len(axes) != len(free):
            return None
        # the gap between two consecutive free stretches that lies inside this contact run
        for k in range(len(free) - 1):
            gap_a, gap_b = free[k][1], free[k + 1][0]
            if gap_b > gap_a and a - FINE_STEP_MM <= gap_a and gap_b <= b + FINE_STEP_MM:
                u, w = axes[k], axes[k + 1]
                break
        else:
            return None
        return float(np.degrees(np.arccos(min(1.0, abs(float(np.dot(u, w)))))))

    supports, faults, afterlength_sides = [], [], []
    for k, (a, b) in enumerate(runs):
        length = b - a
        mask = (ts >= a) & (ts <= b)
        lowest = float(np.min(np.where(inside[mask], clear[mask], 0.0)))
        run = {"from": round((a - t0) / total, 3), "to": round((b - t0) / total, 3),
               "length_mm": round(length, 2), "min_clearance_mm": round(lowest, 2)}
        angle = break_angle(a, b) if k not in ends and length <= support_max else None
        if k in ends:
            supports.append({**run, "kind": ends[k]})
        elif angle is not None and angle >= BREAK_MIN_DEG:
            # #995 review: the string changes direction here (by less than the 2 degree collinear
            # limit): a bridge with a shallow afterlength, or not. Ambiguous, so it ends the
            # speaking part and nothing on its afterlength side is reported as a fault.
            supports.append({**run, "kind": "ambiguous_termination", "break_angle_deg": round(angle, 2)})
            afterlength_sides.append((a, t0) if a - t0 < t1 - b else (b, t1))
        elif intermediate_bridges and length <= support_max:
            supports.append({**run, "kind": "intermediate_bridge"})
        else:
            faults.append({**run, "kind": "contact"})
    if afterlength_sides:
        def on_afterlength(f: Mapping[str, Any]) -> bool:
            a, b = t0 + f["from"] * total, t0 + f["to"] * total
            return any(min(edge, end) <= a and b <= max(edge, end) for edge, end in afterlength_sides)
        faults = [f for f in faults if not on_afterlength(f)]
    bounds = sorted([(t0, t0)] + [(t0 + r["from"] * total, t0 + r["to"] * total) for r in supports] + [(t1, t1)])
    speaking = max(b_start - a_end for (_, a_end), (b_start, _) in zip(bounds, bounds[1:]))
    away = inside & np.isfinite(clear)
    for r in supports:
        away &= ~((ts >= t0 + r["from"] * total - 1e-6) & (ts <= t0 + r["to"] * total + 1e-6))
    free_clear = clear[away]
    return {"length_mm": total, "speaking_length_mm": float(max(speaking, 0.0)), "supports": supports,
            "faults": faults, "samples": int(len(ts)),
            "min_free_clearance_mm": float(free_clear.min()) if len(free_clear) else None}


def _explain(check: str, *, measured, threshold, unit: str, requires: str, body_id: str | None,
             detail: str, **extra: Any) -> dict[str, Any]:
    return {"check": check, "measured": measured, "threshold": threshold, "unit": unit,
            "requires": requires, "body_id": body_id, "detail": detail, **extra}


def _intermediate_bridges(constraints: Mapping[str, Any]) -> bool:
    """Whether the spec declares bridges part-way along the strings (a guzheng's moveable
    bridges, a multi-bridge zither's bridge rings)."""
    return bool(constraints.get("bridges")) or any(str(key).startswith("bridge_ring") for key in constraints)


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
    on_string = np.zeros(len(mesh.faces), dtype=bool)
    centroids = mesh.triangles_center
    for st in strings:
        on_string[st["faces"]] = True
        # faces on the string's own surface that were not grouped (cut by a union) are the
        # string, not the rest of the assembly
        rel = centroids - st["center"]
        t = rel @ st["axis"]
        radial = np.linalg.norm(rel - np.outer(t, st["axis"]), axis=1)
        on_string |= (radial <= st["diameter_mm"] / 2.0 + 0.35) & (t >= st["t0"] - 1.0) & (t <= st["t1"] + 1.0)
    rest_faces = np.nonzero(~on_string)[0]
    rest = mesh.submesh([rest_faces], append=True) if len(rest_faces) else None
    intermediate = _intermediate_bridges(constraints)
    profiles = [string_profile(rest, st, intermediate_bridges=intermediate) for st in strings]
    failures: list[dict] = []

    count, sympathetic = declared_count(constraints)
    if count is not None and not count <= len(strings) <= count + sympathetic:
        allowed = f"{count}" if not sympathetic else f"{count}-{count + sympathetic}"
        failures.append(_explain(
            "string_count", measured=len(strings), threshold=count, unit="strings",
            requires=f"measured in [{allowed}]" + (" (sympathetic strings allowed)" if sympathetic else ""),
            body_id="assembly",
            detail=f"{len(strings)} strings detected (long, slender, thinner than {max_diameter:g} mm; "
                   f"{found['segments']} free stretches merged along their axes), {allowed} declared"
                   + ("; strings fused into a thicker part or omitted are not detected" if len(strings) < count else "")))

    speaking = [p["speaking_length_mm"] for p in profiles]
    window = declared_lengths(constraints)
    if window and speaking:
        if "scale" in window:
            lo_ok, hi_ok = window["scale"] * (1 - LENGTH_TOL_BELOW), window["scale"] * (1 + LENGTH_TOL_ABOVE)
            what = f"{window['source']} declares {window['scale']:g} mm"
        else:
            lo_ok = window["shortest"] * (1 - LENGTH_TOL_BELOW)
            hi_ok = window["longest"] * (1 + LENGTH_TOL_ABOVE)
            what = f"{window['source']} declares {window['shortest']:g}-{window['longest']:g} mm"
            # the declared range must also be spanned: shortest and longest near their ends
            for which, measured, declared in (("shortest string", min(speaking), window["shortest"]),
                                              ("longest string", max(speaking), window["longest"])):
                lo, hi = declared * (1 - LENGTH_TOL_BELOW), declared * (1 + LENGTH_TOL_ABOVE)
                if not lo <= measured <= hi:
                    failures.append(_explain(
                        "string_length", measured=round(measured, 1), threshold=declared, unit="mm",
                        requires=f"{lo:.0f} <= measured <= {hi:.0f} "
                                 f"(declared -{LENGTH_TOL_BELOW:.0%}/+{LENGTH_TOL_ABOVE:.0%})",
                        body_id="assembly", which=which,
                        detail=f"{which} speaks over {measured:.0f} mm; {window['source']} declares "
                               f"{declared:g} mm ({measured / declared - 1:+.0%})"))
        outside = [(index, measured) for index, measured in enumerate(speaking) if not lo_ok <= measured <= hi_ok]
        if outside:
            centre = (lo_ok + hi_ok) / 2.0
            worst_index, worst = max(outside, key=lambda item: abs(item[1] - centre))
            listed = ", ".join(f"string_{i} {m:.0f} mm" for i, m in outside[:12])
            failures.append(_explain(
                "string_length", measured=round(worst, 1),
                threshold=window.get("scale") or [window["shortest"], window["longest"]], unit="mm",
                requires=f"{lo_ok:.0f} <= measured <= {hi_ok:.0f} for every string "
                         f"(declared -{LENGTH_TOL_BELOW:.0%}/+{LENGTH_TOL_ABOVE:.0%})",
                body_id=f"string_{worst_index}", which="every string",
                strings=[{"body_id": f"string_{i}", "speaking_length_mm": round(m, 1)} for i, m in outside],
                detail=f"{len(outside)} of {len(speaking)} strings speak outside the window between their "
                       f"supports ({listed}{', ...' if len(outside) > 12 else ''}); {what}"))

    per_string = []
    for index, (st, prof) in enumerate(zip(strings, profiles)):
        body = f"string_{index}"
        per_string.append({"body_id": body, "length_mm": round(st["length_mm"], 1),
                           "speaking_length_mm": round(prof["speaking_length_mm"], 1),
                           "diameter_mm": round(st["diameter_mm"], 2), "segments": st["segments"],
                           "min_clearance_mm": None if prof["min_free_clearance_mm"] is None
                           else round(prof["min_free_clearance_mm"], 2),
                           "supports": prof["supports"], "contacts": prof["faults"],
                           "runout_angles_deg": st.get("runout_angles_deg", []),
                           "unsupported_runouts": st.get("unsupported_runouts", 0)})
        if prof["faults"]:
            worst = min(prof["faults"], key=lambda f: f["min_clearance_mm"])
            where = ", ".join(f"{f['from']:.0%}-{f['to']:.0%}" for f in prof["faults"])
            failures.append(_explain(
                "string_clearance", measured=worst["min_clearance_mm"], threshold=MIN_CLEARANCE_MM, unit="mm",
                requires="clearance >= threshold along the whole path except at its terminating "
                         "supports (the outermost contact at each end)"
                         + (" and declared intermediate bridges" if intermediate else ""),
                body_id=body, contacts=prof["faults"],
                detail=f"{body} ({st['length_mm']:.0f} mm) touches or comes within {MIN_CLEARANCE_MM:g} mm of "
                       f"the rest of the assembly away from its supports at {where} of its path "
                       f"(closest {worst['min_clearance_mm']:+.2f} mm): it lies on or against the "
                       f"soundboard or body"))

    lengths = [st["length_mm"] for st in strings]
    # #995 review: what the check cannot judge is reported, never failed
    ambiguous = [{"body_id": f"string_{i}", **r} for i, prof in enumerate(profiles)
                 for r in prof["supports"] if r["kind"] == "ambiguous_termination"]
    unsupported = [{"body_id": f"string_{i}", "angles_deg": [a for a in st.get("runout_angles_deg", [])
                                                            if a > RUNOUT_MAX_DEG]}
                   for i, st in enumerate(strings) if st.get("unsupported_runouts")]
    return {
        **base,
        "status": "inconsistent" if failures else "consistent",
        "family": FAMILY,
        "failures": failures,
        "detected": len(strings),
        "ambiguous_terminations": ambiguous,
        "unsupported_runouts": unsupported,
        "declared": {"string_count": count, "sympathetic_string_count": sympathetic or None,
                     "lengths": window, "intermediate_bridges": intermediate},
        "strings": per_string,
        "measured": {"thin_faces": found["thin_faces"], "faces": found["faces"], "segments": found["segments"],
                     "max_diameter_mm": max_diameter,
                     "length_range_mm": [round(min(lengths), 1), round(max(lengths), 1)] if lengths else None,
                     "speaking_length_range_mm": [round(min(speaking), 1), round(max(speaking), 1)]
                     if speaking else None},
        "method": "shape-diameter thin faces grouped by adjacency; long slender groups merged along their "
                  "axes into strings; contacts sampled along each string's whole path",
        "assumptions": [
            f"a string is thinner than {max_diameter:g} mm, its free stretches at least "
            f"{MIN_STRING_LENGTH_MM:g} mm long and {MIN_ASPECT:g}x longer than wide, and straight",
            f"terminating supports: the outermost contact at each end, an anchor (end contact up to "
            f"max({ANCHOR_MAX_MM:g} mm, {ANCHOR_MAX_FRAC:.0%})) or a support (up to max({SUPPORT_MAX_MM:g} mm, "
            f"{SUPPORT_MAX_FRAC:.0%}))" + ("; declared intermediate bridges between them" if intermediate else ""),
            "speaking length = longest free interval between neighbouring supports or anchors",
            f"clearance >= {MIN_CLEARANCE_MM:g} mm everywhere else on the path",
            f"a short contact where the string bends by {BREAK_MIN_DEG:g}-2 degrees is an ambiguous "
            "termination (a bridge with a shallow afterlength, or not): it ends the speaking length and "
            "is reported, never failed",
            f"run-outs bent {RUNOUT_MAX_DEG:g}-{RUNOUT_LINK_MAX_DEG:g} degrees belong to their string but are "
            "outside the calibrated range: reported as unsupported_runouts, never counted or failed",
            "not modelled: tension, gauge, break angle, frets, action",
        ],
    }
