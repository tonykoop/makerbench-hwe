"""Advisory assembly interface fit: floating parts and part-to-part interference (#982, epic #978).

**Advisory only.** The result is labelled ``advisory`` and never changes a sub-score, the
objective pass rate, pass/fail, the blind series or Elo. It rides along under
``objective["advisory"]["assembly_fit"]`` and is reported per tier in ``advisory_report.json``.
It extends the declared #797 interface checks (``topology.py``) from declared sub-volumes to
how the parts of an assembly meet each other.

Modelled specs
    ``assembly: true``. Every other spec reports ``not modelled``. An assembly that arrives as
    one fused body (OpenSCAD unions every top-level object) has no parts to compare and
    reports ``not measurable``.

Parts
    The connected outward-facing bodies of the candidate's mesh, in the gate's own split order
    (``body_N`` matches the gate's failure explanations). An inward-facing closed shell
    (negative signed volume) is the cavity of a hollow part only when it lies inside that
    part's material. It is then kept WITH its part (``void_shells``), so distances and booleans
    see the hollow part as hollow. An inward-facing shell that no part encloses, or that runs
    into another part, is an inverted solid: the geometry is reported ``not measurable``.

Measurement (#982 review)
    Each part is a manifold3d solid. Gaps are measured triangle by triangle with
    ``Manifold.min_gap``, a bounded search in C++ with no point sampling and no Python-side
    candidate arrays. Interference is the volume of the pair's manifold intersection. Every
    comparison uses the raw measurement; only the reported values are rounded.

Checks (each failure is explained in the #903 shape)
    ``floating_part``
        Parts are grouped by contact: two parts are in contact when they overlap or their
        surfaces come within ``contact_tolerance_mm`` (default ``CONTACT_TOLERANCE_MM``) of
        each other. The group holding the most material is the assembly; every part outside it
        floats. Its gap to the nearest part outside its group is measured triangle by triangle.
        On meshes too dense for that (``EXACT_GAP_FACE_PAIRS``) it is a labelled lower bound with
        an upper bound, both from the closest vertex pair.
    ``part_interference``
        Two parts interpenetrate when their shared volume exceeds ``interference_tolerance_mm3``
        AND its mean thickness (``2 V / A``, the interference depth) exceeds
        ``interference_depth_tolerance_mm``. The default depth allowance
        (``INTERFERENCE_DEPTH_TOLERANCE_MM``, 0.2 mm) is the press-fit interference range of
        printed parts, so a press fit over a long engagement passes. A neck running through
        the bowl wall does not. A spec that declares one of the two tolerances is judged on
        it alone, and the other defaults to zero.

Incomplete
    A pair within reach of each other whose overlap or gap cannot be measured (a
    non-watertight part, a failed boolean) makes the result ``incomplete`` unless another
    check already fails it. It is never reported ``consistent``.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import trimesh

LABEL = "advisory"
NOT_MODELLED = "not modelled"
FAMILY = "assembly_fit"
CONTACT_TOLERANCE_MM = 0.5
INTERFERENCE_TOLERANCE_MM3 = 1.0
INTERFERENCE_DEPTH_TOLERANCE_MM = 0.2
#: Face-pair product up to which a floating part's reported gap is measured triangle by triangle.
EXACT_GAP_FACE_PAIRS = 5e7
MAX_PARTS = 40
CAVITY_PROBE_POINTS = 64


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def modelled_reason(spec: Mapping[str, Any]) -> str | None:
    if not spec.get("assembly"):
        return "not an assembly task (assembly: true not declared)"
    return None


def _declared(spec: Mapping[str, Any], key: str) -> float | None:
    value = _num(spec.get(key))
    return value if value is not None else _num((spec.get("constraints") or {}).get(key))


def _manifold(mesh: trimesh.Trimesh):
    """The part as a manifold3d solid, or ``None`` when it is not a valid manifold."""
    import manifold3d

    if not mesh.is_watertight:
        return None
    try:
        solid = manifold3d.Manifold(manifold3d.Mesh64(
            vert_properties=np.ascontiguousarray(mesh.vertices, dtype=np.float64),
            tri_verts=np.ascontiguousarray(mesh.faces, dtype=np.uint64)))
    except Exception:  # noqa: BLE001 - an unbuildable part is reported, never raised
        return None
    if solid.status() != manifold3d.Error.NoError or solid.is_empty():
        return None
    return solid


def _probe(shell: trimesh.Trimesh) -> np.ndarray:
    """A few deterministic points on a shell (its vertices, evenly spaced)."""
    vertices = shell.vertices
    if len(vertices) <= CAVITY_PROBE_POINTS:
        return vertices
    return vertices[np.linspace(0, len(vertices) - 1, CAVITY_PROBE_POINTS).astype(int)]


def _parts_and_cavities(bodies: list[trimesh.Trimesh]) -> tuple[list[int], dict[int, list[int]], list[int]]:
    """``(outward body indices, {owner: [cavity indices]}, inverted body indices)``.

    A negative-volume closed shell is a cavity of the smallest outward shell that encloses it
    (#982 review), never simply dropped; one no outward shell encloses is an inverted solid.
    """
    outward = [i for i, b in enumerate(bodies) if not (b.is_watertight and float(b.volume) < 0.0)]
    negative = [i for i in range(len(bodies)) if i not in outward]
    owners: dict[int, list[int]] = {}
    inverted: list[int] = []
    by_size = sorted((i for i in outward if bodies[i].is_watertight), key=lambda i: abs(float(bodies[i].volume)))
    for k in negative:
        points = _probe(bodies[k])
        lo, hi = bodies[k].bounds
        owner = None
        for i in by_size:
            blo, bhi = bodies[i].bounds
            if np.any(lo < blo) or np.any(hi > bhi):
                continue
            if bool(np.all(bodies[i].contains(points))):
                owner = i
                break
        if owner is None:
            inverted.append(k)
        else:
            owners.setdefault(owner, []).append(k)
    return outward, owners, inverted


def _bbox_gap(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    """Lower bound on the surface gap: the distance between the axis-aligned boxes."""
    (alo, ahi), (blo, bhi) = a.bounds, b.bounds
    per_axis = np.maximum(0.0, np.maximum(blo - ahi, alo - bhi))
    return float(np.linalg.norm(per_axis))


def _gap(a, b, search: float) -> float | None:
    """Triangle-level gap between two solids, capped at ``search`` (``None`` on failure)."""
    try:
        return float(a.min_gap(b, float(search)))
    except Exception:  # noqa: BLE001
        return None


def _overlap(a, b) -> tuple[float, float] | None:
    """``(shared volume mm3, shared surface area mm2)`` of two solids, or ``None`` on failure."""
    import manifold3d

    try:
        shared = a ^ b
        if shared.status() != manifold3d.Error.NoError:
            return None
        if shared.is_empty():
            return 0.0, 0.0
        return float(shared.volume()), float(shared.surface_area())
    except Exception:  # noqa: BLE001 - an unmeasurable pair is reported, never raised
        return None


def _explain(check: str, *, measured, threshold, unit: str, requires: str, body_id: str | None,
             detail: str, **extra: Any) -> dict[str, Any]:
    return {"check": check, "measured": measured, "threshold": threshold, "unit": unit,
            "requires": requires, "body_id": body_id, "detail": detail, **extra}


def advise(spec: Mapping[str, Any], mesh: trimesh.Trimesh) -> dict[str, Any]:
    """The labelled assembly-fit advisory for one compiled candidate. Never affects scoring."""
    base: dict[str, Any] = {"label": LABEL, "affects_scoring": False}
    reason = modelled_reason(spec)
    if reason:
        return {**base, "status": NOT_MODELLED, "reason": reason}
    contact_tol = _declared(spec, "contact_tolerance_mm")
    contact_tol = CONTACT_TOLERANCE_MM if contact_tol is None else contact_tol
    volume_tol = _declared(spec, "interference_tolerance_mm3")
    depth_tol = _declared(spec, "interference_depth_tolerance_mm")
    if volume_tol is None and depth_tol is None:
        volume_tol, depth_tol = INTERFERENCE_TOLERANCE_MM3, INTERFERENCE_DEPTH_TOLERANCE_MM
    volume_tol = 0.0 if volume_tol is None else volume_tol
    depth_tol = 0.0 if depth_tol is None else depth_tol
    tolerances = {"contact_tolerance_mm": contact_tol, "interference_tolerance_mm3": volume_tol,
                  "interference_depth_tolerance_mm": depth_tol}

    bodies = list(mesh.split(only_watertight=False)) if len(mesh.faces) else []
    outward, owners, inverted = _parts_and_cavities(bodies)
    void_shells = sum(len(v) for v in owners.values())
    common = {**base, "family": FAMILY, "failures": [], "void_shells": void_shells, "tolerances": tolerances}
    if inverted:
        return {**common, "status": "not measurable", "parts": len(outward),
                "inverted_shells": [f"body_{k}" for k in inverted],
                "error": f"{len(inverted)} inward-facing shell(s) ({', '.join(f'body_{k}' for k in inverted)}) "
                         "are not the cavity of any part: inverted solids, so overlaps cannot be measured"}
    if len(outward) < 2:
        return {**common, "status": "not measurable", "parts": len(outward),
                "error": "the assembly arrives as one fused body, so its parts cannot be told apart"}
    if len(outward) > MAX_PARTS:
        return {**common, "status": "not measurable", "parts": len(outward),
                "error": f"{len(outward)} parts (more than {MAX_PARTS} are not compared)"}

    ids = [f"body_{i}" for i in outward]
    parts = [trimesh.util.concatenate([bodies[i]] + [bodies[k] for k in owners.get(i, [])])
             if owners.get(i) else bodies[i] for i in outward]
    # a cavity must lie in its owner's material only: an inward-facing shell that also runs
    # into another part is an inverted solid, not a cavity (#982 review)
    for owner, cavities in owners.items():
        for k in cavities:
            points = _probe(bodies[k])
            for m, part in enumerate(parts):
                if outward[m] == owner or not part.is_watertight:
                    continue
                if np.any(part.contains(points)):
                    return {**common, "status": "not measurable", "parts": len(outward),
                            "inverted_shells": [f"body_{k}"],
                            "error": f"inward-facing shell body_{k} lies inside {ids[m]}, not only in "
                                     f"body_{owner}: an inverted solid, so overlaps cannot be measured"}
    solids = [_manifold(p) for p in parts]

    n = len(parts)
    gap = np.full((n, n), np.inf)        # raw measured gap, or a lower bound where `exact` is False
    exact = np.zeros((n, n), dtype=bool)
    overlaps: list[dict[str, Any]] = []
    raw_overlaps: list[tuple[int, int, float, float]] = []
    unmeasured: list[list[str]] = []
    search = max(contact_tol, 1e-6) * 2.0
    for i in range(n):
        for j in range(i + 1, n):
            lower = _bbox_gap(parts[i], parts[j])
            if lower > contact_tol:
                gap[i, j] = gap[j, i] = lower  # out of reach; a lower bound only
                continue
            if solids[i] is None or solids[j] is None:
                unmeasured.append([ids[i], ids[j]])
                gap[i, j] = gap[j, i] = 0.0  # cannot tell: never reported floating because of it
                continue
            shared = _overlap(solids[i], solids[j]) if lower == 0.0 else (0.0, 0.0)
            if shared is None:
                unmeasured.append([ids[i], ids[j]])
                gap[i, j] = gap[j, i] = 0.0
                continue
            volume, area = shared
            if volume > 0.0:
                depth = 2.0 * volume / area if area > 0.0 else 0.0
                raw_overlaps.append((i, j, volume, depth))
                overlaps.append({"parts": [ids[i], ids[j]], "volume_mm3": round(volume, 6),
                                 "depth_mm": round(depth, 4)})
                gap[i, j] = gap[j, i] = 0.0
                exact[i, j] = exact[j, i] = True
                continue
            measured = _gap(solids[i], solids[j], search)
            if measured is None:
                unmeasured.append([ids[i], ids[j]])
                gap[i, j] = gap[j, i] = 0.0
                continue
            # min_gap caps at the search length: at the cap the gap is only a lower bound
            gap[i, j] = gap[j, i] = measured
            exact[i, j] = exact[j, i] = measured < search

    # contact groups (union-find over pairs within tolerance, raw values)
    parent = list(range(n))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i in range(n):
        for j in range(i + 1, n):
            if gap[i, j] <= contact_tol:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for k in range(n):
        groups.setdefault(find(k), []).append(k)
    # the group holding the most material (then the most parts) is the assembly: two pegs that
    # touch each other but float above the body do not outvote the body; ties go to the lowest index
    volumes = [float(s.volume()) if s is not None else 0.0 for s in solids]
    main = max(groups.values(), key=lambda g: (sum(volumes[k] for k in g), len(g), -min(g)))

    failures: list[dict[str, Any]] = []
    floating = []
    for k in range(n):
        if k in main:
            continue
        own = groups[find(k)]
        outside = [m for m in range(n) if m not in own]
        nearest, value, is_exact, upper = _nearest_gap(k, outside, parts, solids, gap, exact)
        kind = "measured" if is_exact else "lower_bound"
        bounds = {} if is_exact or upper is None else {"gap_upper_bound_mm": round(upper, 3)}
        floating.append({"body_id": ids[k], "nearest": ids[nearest], "gap_mm": round(value, 3),
                         "gap_kind": kind, **bounds, "group_size": len(own)})
        failures.append(_explain(
            "floating_part", measured=round(value, 3), threshold=contact_tol, unit="mm",
            requires="every part within the contact tolerance of the rest of the assembly", body_id=ids[k],
            nearest=ids[nearest], gap_kind=kind, **bounds,
            detail=f"{ids[k]} ({'alone' if len(own) == 1 else f'in a group of {len(own)} parts'}) is "
                   f"{'' if is_exact else 'at least '}{value:.2f} mm from {ids[nearest]}, the nearest part "
                   f"of the rest of the assembly (contact tolerance {contact_tol:g} mm): it floats"
                   + ("" if is_exact else " (a lower bound: the mesh is too dense to measure this gap "
                                          "triangle by triangle"
                      + (f"; at most {upper:.2f} mm" if upper is not None else "") + ")")))
    for i, j, volume, depth in raw_overlaps:
        if volume <= volume_tol or depth <= depth_tol:
            continue
        a, b = ids[i], ids[j]
        failures.append(_explain(
            "part_interference", measured=round(volume, 6), threshold=volume_tol, unit="mm3",
            requires=f"shared volume <= threshold, or its mean thickness <= {depth_tol:g} mm, "
                     "for every pair of parts", body_id=a, other_body_id=b,
            depth_mm=round(depth, 4), depth_threshold_mm=depth_tol,
            detail=f"{a} and {b} interpenetrate by {volume:.4g} mm3 of shared volume, {depth:.3g} mm "
                   f"deep on average (tolerances {volume_tol:g} mm3, {depth_tol:g} mm): one part runs "
                   f"into the other"))

    status = "inconsistent" if failures else ("incomplete" if unmeasured else "consistent")
    return {
        **base,
        "status": status,
        "family": FAMILY,
        "failures": failures,
        "parts": n,
        "void_shells": void_shells,
        "groups": sorted(len(g) for g in groups.values())[::-1],
        "floating": floating,
        "overlaps": overlaps,
        "unmeasured_pairs": unmeasured,
        "tolerances": tolerances,
        **({"incomplete_reason": f"{len(unmeasured)} part pair(s) within reach of each other could not be "
                                 "measured (non-watertight part or failed boolean)"} if unmeasured else {}),
        "method": "connected outward bodies as parts, each with its enclosed cavity shells; triangle-level "
                  "gaps (manifold3d min_gap); contact groups by gap <= tolerance; interference as the "
                  "manifold intersection volume and its mean thickness 2V/A",
        "assumptions": [
            f"parts in contact when they overlap or come within {contact_tol:g} mm; the contact group "
            "holding the most material is the assembly",
            f"interference allowed up to {volume_tol:g} mm3 of shared volume or {depth_tol:g} mm mean "
            "thickness per pair (the default depth allowance covers printed press fits)",
            "not modelled: intended clearance fits, fasteners, glue lines, part function",
        ],
    }


def _vertex_bracket(a: trimesh.Trimesh, b: trimesh.Trimesh, lower: float) -> tuple[float, float]:
    """``(lower bound, upper bound)`` on the surface gap from the closest vertex pair (a KD-tree,
    linear memory). Every surface point lies within its mesh's longest edge of a vertex, so the
    true gap is at least the vertex gap minus both longest edges, and at most the vertex gap."""
    from scipy.spatial import cKDTree

    distance, _ = cKDTree(b.vertices).query(a.vertices, k=1)
    upper = float(np.min(distance))
    reach = float(a.edges_unique_length.max()) + float(b.edges_unique_length.max())
    return max(lower, upper - reach), upper


def _nearest_gap(k: int, outside: list[int], parts: list, solids: list, gap: np.ndarray,
                 exact: np.ndarray) -> tuple[int, float, bool, float | None]:
    """The nearest part outside ``k``'s group: ``(index, gap, measured?, upper bound)``.

    Candidates are taken in order of their lower bound. A pair whose face-pair product is
    within ``EXACT_GAP_FACE_PAIRS`` is measured triangle by triangle (``min_gap``). A denser pair
    gets a vertex bracket instead, reported as a labelled lower bound with its upper bound, so
    the cost stays bounded on dense meshes. The search stops once the next lower bound cannot
    beat the best gap found.
    """
    best: tuple[int, float, bool, float | None] | None = None
    for m in sorted(outside, key=lambda m: (gap[k, m], m)):
        if best is not None and gap[k, m] >= best[1]:
            break
        value, is_exact, upper = float(gap[k, m]), bool(exact[k, m]), None
        if not is_exact:
            dense = len(parts[k].faces) * len(parts[m].faces) > EXACT_GAP_FACE_PAIRS
            if solids[k] is not None and solids[m] is not None and not dense:
                lo, up = _vertex_bracket(parts[k], parts[m], value)
                measured = _gap(solids[k], solids[m], up * (1.0 + 1e-9) + 1e-9)
                if measured is not None:
                    value, is_exact = measured, True
                else:
                    value, upper = lo, up
            else:
                value, upper = _vertex_bracket(parts[k], parts[m], value)
        if best is None or value < best[1]:
            best = (m, value, is_exact, upper)
    assert best is not None
    return best
