"""Topology and interface sub-volume gates for compiled candidates (#797).

Both checks are opt-in per registry spec. A spec that does not declare the
field reports ``not declared``, which is neutral: it adds no sub-score, so an
existing objective pass rate and scoreline stay byte-identical.

Topology
    Betti numbers of the *solid* bounded by a closed, manifold triangle mesh:
    ``b0`` = solid bodies, ``b1`` = independent through-holes/handles (tunnels),
    ``b2`` = enclosed voids. The mesh is split into closed shells. A shell's
    nesting depth (how many other shells contain it) decides its role: even is
    an outer body boundary, odd is a void. Each shell's genus comes from its
    own Euler characteristic, ``g = (2 - (V - E + F)) / 2``, and
    ``b1 = sum(g)``. The solid's Euler characteristic is ``b0 - b1 + b2``.
    A flute tube with N tone holes is ``(1, N + 1, 0)``; a torus is
    ``(1, 1, 0)``.

    Spec: ``"topology": {"betti": [b0, b1, b2]}`` and/or
    ``{"euler_characteristic": chi}``. Exact integer match.

Interfaces
    Declared sub-volumes with a stated tolerance (default ``0.5`` mm), checked
    by point membership on a fixed deterministic grid (no random sampling):

    ``{"kind": "hole", "name", "center_mm": [x,y,z], "axis": [dx,dy,dz],
    "diameter_mm", "depth_mm", "tolerance_mm"?}``
        A cylinder of radius ``d/2 - tol`` around the axis through the centre,
        spanning ``depth_mm``, must contain no material. A ring of radius
        ``d/2 + tol`` at mid-depth must be material. A missing, undersized,
        oversized or displaced (by more than ``tol``) hole fails.

    ``{"kind": "pocket", "name", "min_mm": [..], "max_mm": [..],
    "open_faces": ["+z", ...]?, "tolerance_mm"?}``
        The box shrunk by ``tol`` must contain no material, and every face of
        the box grown by ``tol`` that is not listed in ``open_faces`` must be
        material (the pocket's floor and walls).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import trimesh

NOT_DECLARED = "not declared"
DEFAULT_INTERFACE_TOLERANCE_MM = 0.5
#: Fraction of wall probe points that must be material. Below 1.0 so a probe
#: point that lands exactly on a tessellation facet cannot flip a good part.
WALL_MATERIAL_FRACTION = 0.95
_GRID = 9


def _shell_euler(shell: trimesh.Trimesh) -> int:
    vertices = len(np.unique(shell.faces))
    edges = len(np.unique(np.sort(shell.edges, axis=1), axis=0))
    return int(vertices - edges + len(shell.faces))


def betti_numbers(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Betti numbers and Euler characteristic of the solid a closed mesh bounds.

    Returns ``{"ok": False, "error": ...}`` for a mesh that is not closed and
    manifold, because its topology is undefined.
    """
    if mesh is None or len(mesh.faces) == 0:
        return {"ok": False, "error": "empty mesh"}
    mesh = mesh.copy()
    mesh.merge_vertices()
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        return {"ok": False, "error": "mesh is not closed and manifold; topology undefined"}
    shells = mesh.split(only_watertight=False)
    depths = []
    for i, shell in enumerate(shells):
        probe = shell.vertices[:1]
        depth = sum(
            1 for j, other in enumerate(shells) if j != i and bool(other.contains(probe)[0])
        )
        depths.append(depth)
    genera = []
    for shell in shells:
        twice_genus = 2 - _shell_euler(shell)
        if twice_genus < 0 or twice_genus % 2:
            return {"ok": False, "error": "shell Euler characteristic is not a closed surface's"}
        genera.append(twice_genus // 2)
    b0 = sum(1 for depth in depths if depth % 2 == 0)
    b2 = len(shells) - b0
    b1 = int(sum(genera))
    return {"ok": True, "betti": [b0, b1, b2], "euler_characteristic": b0 - b1 + b2,
            "shells": len(shells)}


def topology_check(mesh: trimesh.Trimesh, spec: Mapping[str, Any]) -> dict[str, Any]:
    declared = spec.get("topology")
    if not declared:
        return {"status": NOT_DECLARED}
    expected: dict[str, Any] = {}
    if "betti" in declared:
        expected["betti"] = [int(v) for v in declared["betti"]]
    if "euler_characteristic" in declared:
        expected["euler_characteristic"] = int(declared["euler_characteristic"])
    if not expected:
        return {"status": "fail", "error": "topology declares neither betti nor euler_characteristic"}
    measured = betti_numbers(mesh)
    if not measured["ok"]:
        return {"status": "fail", "expected": expected, "error": measured["error"]}
    observed = {key: measured[key] for key in expected}
    return {"status": "pass" if observed == expected else "fail",
            "expected": expected, "observed": observed}


def _unit(vector: Sequence[float]) -> np.ndarray:
    v = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(v))
    if v.shape != (3,) or norm == 0.0:
        raise ValueError("axis must be a non-zero 3-vector")
    return v / norm


def _basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def _hole_probes(item: Mapping[str, Any], tol: float) -> tuple[np.ndarray, np.ndarray]:
    centre = np.asarray(item["center_mm"], dtype=float)
    axis = _unit(item["axis"])
    radius = float(item["diameter_mm"]) / 2.0
    depth = float(item["depth_mm"])
    if radius - tol <= 0 or depth <= 0:
        raise ValueError("hole needs diameter_mm/2 > tolerance_mm and depth_mm > 0")
    u, v = _basis(axis)
    angles = np.linspace(0.0, 2.0 * np.pi, 4 * _GRID, endpoint=False)
    heights = np.linspace(-depth / 2.0, depth / 2.0, _GRID)
    radii = np.linspace(0.0, radius - tol, 4)
    core = np.array([
        centre + h * axis + r * (np.cos(a) * u + np.sin(a) * v)
        for h in heights for r in radii for a in angles
    ])
    ring = np.array([
        centre + (radius + tol) * (np.cos(a) * u + np.sin(a) * v) for a in angles
    ])
    return core, ring


_FACES = {"-x": (0, 0), "+x": (0, 1), "-y": (1, 0), "+y": (1, 1), "-z": (2, 0), "+z": (2, 1)}


def _pocket_probes(item: Mapping[str, Any], tol: float) -> tuple[np.ndarray, np.ndarray]:
    lo = np.asarray(item["min_mm"], dtype=float)
    hi = np.asarray(item["max_mm"], dtype=float)
    if lo.shape != (3,) or hi.shape != (3,) or np.any(hi - lo <= 2 * tol):
        raise ValueError("pocket needs min_mm < max_mm by more than 2 * tolerance_mm")
    open_faces = set(item.get("open_faces") or [])
    unknown = open_faces - set(_FACES)
    if unknown:
        raise ValueError(f"unknown open_faces {sorted(unknown)}")
    axes = [np.linspace(lo[k] + tol, hi[k] - tol, _GRID) for k in range(3)]
    core = np.array(np.meshgrid(*axes, indexing="ij")).reshape(3, -1).T
    walls = []
    for face, (dim, side) in sorted(_FACES.items()):
        if face in open_faces:
            continue
        others = [k for k in range(3) if k != dim]
        grid = np.array(np.meshgrid(*(axes[k] for k in others), indexing="ij")).reshape(2, -1).T
        points = np.empty((len(grid), 3))
        points[:, others[0]] = grid[:, 0]
        points[:, others[1]] = grid[:, 1]
        points[:, dim] = (hi[dim] + tol) if side else (lo[dim] - tol)
        walls.append(points)
    return core, (np.vstack(walls) if walls else np.empty((0, 3)))


def interface_check(mesh: trimesh.Trimesh, spec: Mapping[str, Any]) -> dict[str, Any]:
    declared = spec.get("interfaces")
    if not declared:
        return {"status": NOT_DECLARED}
    if not mesh.is_watertight:
        return {"status": "fail", "error": "mesh is not watertight; point membership undefined"}
    results = []
    for index, item in enumerate(declared):
        name = str(item.get("name") or f"interface_{index}")
        kind = item.get("kind")
        tol = float(item.get("tolerance_mm", DEFAULT_INTERFACE_TOLERANCE_MM))
        try:
            if kind == "hole":
                core, wall = _hole_probes(item, tol)
            elif kind == "pocket":
                core, wall = _pocket_probes(item, tol)
            else:
                raise ValueError(f"unknown interface kind {kind!r}")
        except (KeyError, TypeError, ValueError) as exc:
            results.append({"name": name, "kind": kind, "status": "fail",
                            "error": f"invalid declaration: {exc}"})
            continue
        core_material = int(np.count_nonzero(mesh.contains(core)))
        wall_fraction = float(np.mean(mesh.contains(wall))) if len(wall) else 1.0
        ok = core_material == 0 and wall_fraction >= WALL_MATERIAL_FRACTION
        results.append({
            "name": name, "kind": kind, "status": "pass" if ok else "fail",
            "tolerance_mm": tol, "core_points_in_material": core_material,
            "core_points": len(core), "wall_material_fraction": round(wall_fraction, 4),
        })
    status = "pass" if all(r["status"] == "pass" for r in results) else "fail"
    return {"status": status, "interfaces": results}


def declared_checks(mesh: trimesh.Trimesh, spec: Mapping[str, Any]) -> tuple[dict, dict]:
    """``(extra_sub_scores, checks)``. Sub-scores exist only for declared fields."""
    checks = {"topology": topology_check(mesh, spec), "interfaces": interface_check(mesh, spec)}
    sub_scores = {
        key: 1.0 if result["status"] == "pass" else 0.0
        for key, result in checks.items()
        if result["status"] != NOT_DECLARED
    }
    return sub_scores, checks
