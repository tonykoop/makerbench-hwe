"""Measurement helpers for compiled CAD candidates (#794).

Pure-Python, deterministic measurements over a compiled candidate's STL (and
STEP, when the optional OCP kernel is importable): minimum wall thickness,
clearance between two named bodies, volume, bounding box, and section area at an
axis-aligned or arbitrary plane.

Every helper returns a :class:`Measurement`. A measurement is either ``ok`` with
a numeric ``value`` or not ``ok`` with an ``error`` string and ``value=None``.
Invalid input (an empty mesh, a non-watertight or non-manifold mesh, a missing
body name) is always an explicit error, never a guessed number. The design
workbench (#788) and the entrant measure tool (R-5, #798) both call this API.

Nothing here compiles or executes candidate code: inputs are artifacts that a
sandboxed compiler already produced.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import trimesh

from . import geometry

DEFAULT_WALL_SAMPLES = 4000
DEFAULT_CLEARANCE_SAMPLES = 2000
DEFAULT_SEED = 0

# Stated method tolerances. They are what the golden-fixture tests hold the
# estimators to, so a change in method that drifts past them fails CI.
WALL_TOLERANCE_MM = geometry.WALL_MEAS_TOL_MM
WALL_TOLERANCE_REL = 0.01
CLEARANCE_TOLERANCE_MM = 1e-6
SECTION_AREA_TOLERANCE_REL = 0.005

_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


@dataclass(frozen=True)
class Measurement:
    """One measurement result. ``value`` is ``None`` whenever ``ok`` is false."""

    metric: str
    ok: bool
    value: float | list[float] | None
    unit: str
    method: str
    tolerance: str = ""
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fail(metric: str, unit: str, method: str, error: str, **details: Any) -> Measurement:
    return Measurement(metric, False, None, unit, method, error=error, details=details)


def mesh_problem(mesh: trimesh.Trimesh | None) -> str | None:
    """Why a mesh cannot be measured as a solid, or ``None`` when it can."""
    if mesh is None or len(mesh.faces) == 0 or len(mesh.vertices) == 0:
        return "empty mesh: no faces"
    edges = np.sort(mesh.edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    if np.any(counts != 2):
        return (
            "non-manifold mesh: "
            f"{int(np.sum(counts == 1))} boundary edge(s), {int(np.sum(counts > 2))} edge(s) "
            "shared by more than two faces"
        )
    if not mesh.is_winding_consistent:
        return "non-manifold mesh: inconsistent face winding"
    if not mesh.is_watertight:
        return "mesh is not watertight"
    signed_volume = float(mesh.volume)
    if signed_volume == 0.0:
        return "mesh encloses zero volume"
    if signed_volume < 0.0:
        return "mesh is inside-out: faces wind inward (negative signed volume)"
    return None


def load_mesh(path: str | Path, *, linear_deflection_mm: float = 0.01) -> trimesh.Trimesh:
    """Load an STL/OBJ/3MF/GLB (merged) or a STEP file as one triangle mesh.

    Raises ``ValueError`` for an unreadable or empty artifact. STEP needs OCP.
    """
    path = Path(path)
    if path.suffix.lower() in {".step", ".stp"}:
        return load_step_mesh(path, linear_deflection_mm=linear_deflection_mm)
    loaded = trimesh.load(path.as_posix(), force="mesh")
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError(f"{path.name}: not a triangle mesh")
    return loaded


def ocp_available() -> bool:
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return True


def load_step_mesh(path: str | Path, *, linear_deflection_mm: float = 0.01) -> trimesh.Trimesh:
    """Tessellate a STEP B-rep through OCP into a trimesh (host-side parse only)."""
    if not ocp_available():
        raise ValueError("STEP measurement needs the optional OCP kernel (CadQuery extra)")
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    reader = STEPControl_Reader()
    if reader.ReadFile(Path(path).as_posix()) != IFSelect_RetDone:
        raise ValueError(f"{Path(path).name}: OCP could not read the STEP file")
    if reader.TransferRoots() <= 0:
        raise ValueError(f"{Path(path).name}: STEP file has no transferable shape")
    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, linear_deflection_mm, False, 0.5, True)
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            transform = location.Transformation()
            base = len(vertices)
            for i in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(i).Transformed(transform)
                vertices.append([point.X(), point.Y(), point.Z()])
            reversed_face = face.Orientation() == TopAbs_REVERSED
            for i in range(1, triangulation.NbTriangles() + 1):
                a, b, c = triangulation.Triangle(i).Get()
                tri = [base + a - 1, base + b - 1, base + c - 1]
                faces.append([tri[0], tri[2], tri[1]] if reversed_face else tri)
        explorer.Next()
    if not faces:
        raise ValueError(f"{Path(path).name}: STEP shape produced no triangles")
    # Faces share B-rep edges but not node indices; merging welds them.
    mesh = trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=True)
    mesh.merge_vertices()
    return mesh


def split_bodies(mesh: trimesh.Trimesh) -> dict[str, trimesh.Trimesh]:
    """Name each connected component ``body_<i>``, ordered by bbox min (x, y, z).

    The ordering is geometric, not file-order, so names are stable across
    exporters that emit the same bodies in a different order.
    """
    parts = mesh.split(only_watertight=False)
    parts = sorted(parts, key=lambda part: tuple(np.round(part.bounds[0], 6)))
    return {f"body_{i}": part for i, part in enumerate(parts)}


def load_bodies(path: str | Path) -> dict[str, trimesh.Trimesh]:
    """Named bodies from a scene file (its geometry names) or split components."""
    loaded = trimesh.load(Path(path).as_posix())
    if isinstance(loaded, trimesh.Scene) and len(loaded.geometry) > 1:
        return {name: loaded.geometry[name] for name in sorted(loaded.geometry)}
    mesh = loaded.to_mesh() if isinstance(loaded, trimesh.Scene) else loaded
    return split_bodies(mesh)


def volume(mesh: trimesh.Trimesh) -> Measurement:
    method = "signed tetrahedron sum over the closed triangle mesh"
    problem = mesh_problem(mesh)
    if problem:
        return _fail("volume", "mm3", method, problem)
    return Measurement("volume", True, float(abs(mesh.volume)), "mm3", method)


def bbox(mesh: trimesh.Trimesh) -> Measurement:
    """Axis-aligned bounding box. ``value`` is the extents; min/max in details."""
    method = "axis-aligned vertex bounds"
    if mesh is None or len(mesh.faces) == 0:
        return _fail("bbox", "mm", method, "empty mesh: no faces")
    lo, hi = mesh.bounds
    return Measurement(
        "bbox",
        True,
        [float(v) for v in hi - lo],
        "mm",
        method,
        details={"min": [float(v) for v in lo], "max": [float(v) for v in hi]},
    )


def _plane(axis: str | None, offset_mm: float, origin: Sequence[float] | None,
           normal: Sequence[float] | None) -> tuple[np.ndarray, np.ndarray]:
    if axis is not None:
        if axis not in _AXES:
            raise ValueError(f"axis must be one of x, y, z (got {axis!r})")
        unit = np.asarray(_AXES[axis])
        return unit * float(offset_mm), unit
    if origin is None or normal is None:
        raise ValueError("pass either axis=/offset_mm= or origin= and normal=")
    n = np.asarray(normal, dtype=float)
    length = float(np.linalg.norm(n))
    if n.shape != (3,) or length == 0.0:
        raise ValueError("normal must be a non-zero 3-vector")
    return np.asarray(origin, dtype=float), n / length


def section_area(
    mesh: trimesh.Trimesh,
    *,
    axis: str | None = None,
    offset_mm: float = 0.0,
    origin: Sequence[float] | None = None,
    normal: Sequence[float] | None = None,
) -> Measurement:
    """Solid cross-section area where a plane cuts the mesh.

    Use ``axis="z", offset_mm=12`` for an axis-aligned plane or ``origin`` and
    ``normal`` for an arbitrary one. Holes are subtracted. A plane that misses
    the solid is a true ``0.0``, reported with ``details["intersects"] = False``.
    """
    method = "mesh-plane intersection loops, filled polygons with holes subtracted"
    tolerance = f"tessellation-limited; fixtures hold {SECTION_AREA_TOLERANCE_REL:.1%}"
    problem = mesh_problem(mesh)
    try:
        plane_origin, plane_normal = _plane(axis, offset_mm, origin, normal)
    except ValueError as exc:
        return _fail("section_area", "mm2", method, str(exc))
    plane = {"origin": plane_origin.tolist(), "normal": plane_normal.tolist()}
    if problem:
        return _fail("section_area", "mm2", method, problem, plane=plane)
    section = mesh.section(plane_origin=plane_origin, plane_normal=plane_normal)
    if section is None:
        return Measurement("section_area", True, 0.0, "mm2", method, tolerance,
                           details={"plane": plane, "intersects": False, "loops": 0})
    path_2d, _ = section.to_2D()
    polygons = path_2d.polygons_full
    if len(section.entities) and not polygons:
        return _fail("section_area", "mm2", method,
                     "section loops did not close into polygons", plane=plane)
    area = float(sum(polygon.area for polygon in polygons))
    return Measurement(
        "section_area", True, area, "mm2", method, tolerance,
        details={"plane": plane, "intersects": True, "loops": len(section.entities),
                 "polygons": len(polygons)},
    )


def min_wall_thickness(
    mesh: trimesh.Trimesh,
    *,
    samples: int = DEFAULT_WALL_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> Measurement:
    """Minimum wall thickness by inward ray sampling.

    ``samples`` surface points are drawn with a fixed ``seed``. From each, a ray
    is cast along the inward face normal (from 1e-3 mm inside the surface) and
    the distance to the first opposite surface is recorded; the minimum is
    reported. A thin wall is found only if a sample lands on it, so the estimate
    is an upper bound on the true minimum for features smaller than the sample
    spacing, and slightly under the true wall (by the 1e-3 mm offset plus facet
    chord error) on resolved walls.
    """
    method = f"inward ray sampling from {samples} seeded surface points (seed={seed})"
    tolerance = f"±{WALL_TOLERANCE_MM} mm or ±{WALL_TOLERANCE_REL:.0%}, whichever is larger"
    problem = mesh_problem(mesh)
    if problem:
        return _fail("min_wall_thickness", "mm", method, problem)
    if samples < 1:
        return _fail("min_wall_thickness", "mm", method, "samples must be >= 1")
    value = geometry.estimate_min_wall_mm(mesh, samples=samples, seed=seed)
    if not np.isfinite(value) or value <= 0.0:
        return _fail("min_wall_thickness", "mm", method,
                     "no inward ray hit an opposite surface")
    return Measurement("min_wall_thickness", True, float(value), "mm", method, tolerance,
                       details={"samples": samples, "seed": seed})


def clearance(
    bodies: Mapping[str, trimesh.Trimesh],
    body_a: str,
    body_b: str,
    *,
    samples: int = DEFAULT_CLEARANCE_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> Measurement:
    """Minimum gap between two named bodies.

    The distance is the minimum closest-point distance from every vertex plus
    ``samples`` seeded surface points of each body to the other body's surface,
    taken both ways. Vertices make face-to-face and vertex-to-face gaps exact;
    the samples cover edge-to-edge cases. Overlapping solids report
    ``value=0.0`` with ``details["interfering"] = True`` and the overlap volume.
    """
    method = (
        f"closest-point distance from vertices + {samples} seeded surface samples, "
        f"both directions (seed={seed}); overlap by manifold boolean intersection"
    )
    for name in (body_a, body_b):
        if name not in bodies:
            return _fail("clearance", "mm", method, f"unknown body {name!r}",
                         available=sorted(bodies))
    if body_a == body_b:
        return _fail("clearance", "mm", method, "clearance needs two different bodies")
    a, b = bodies[body_a], bodies[body_b]
    for name, mesh in ((body_a, a), (body_b, b)):
        problem = mesh_problem(mesh)
        if problem:
            return _fail("clearance", "mm", method, f"{name}: {problem}")
    overlap = geometry.interference_volume_mm3(a, b)
    if not np.isfinite(overlap):
        return _fail("clearance", "mm", method, "boolean intersection failed")
    details: dict[str, Any] = {"bodies": [body_a, body_b], "samples": samples, "seed": seed}
    if overlap > 0.0 or b.contains(a.vertices[:1]).any() or a.contains(b.vertices[:1]).any():
        details.update(interfering=True, overlap_mm3=float(overlap))
        return Measurement("clearance", True, 0.0, "mm", method, details=details)
    distance = min(_one_way_distance(a, b, samples, seed), _one_way_distance(b, a, samples, seed))
    details["interfering"] = False
    return Measurement("clearance", True, float(distance), "mm", method,
                       f"exact for face/vertex gaps; sampled for edge-edge (≥{samples} pts)",
                       details=details)


def _one_way_distance(source: trimesh.Trimesh, target: trimesh.Trimesh, samples: int,
                      seed: int) -> float:
    points = np.asarray(source.vertices, dtype=float)
    if samples > 0:
        sampled, _ = trimesh.sample.sample_surface(source, samples, seed=seed)
        points = np.vstack([points, sampled])
    _, distances, _ = trimesh.proximity.closest_point(target, points)
    return float(np.min(distances))


def measure_candidate(
    path: str | Path,
    *,
    sections: Sequence[Mapping[str, Any]] = (),
    wall_samples: int = DEFAULT_WALL_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Measure one compiled artifact: volume, bbox, min wall, and any sections.

    ``sections`` entries are keyword dicts for :func:`section_area`. The result
    is JSON-serialisable and every entry carries its own ok/error.
    """
    try:
        mesh = load_mesh(path)
    except (ValueError, OSError) as exc:
        error = _fail("load", "", "trimesh/OCP load", str(exc)).to_dict()
        return {"artifact": Path(path).name, "ok": False, "error": str(exc), "load": error}
    results = [volume(mesh), bbox(mesh), min_wall_thickness(mesh, samples=wall_samples, seed=seed)]
    results.extend(section_area(mesh, **dict(spec)) for spec in sections)
    errors = [f"{result.metric}: {result.error}" for result in results if not result.ok]
    return {
        "artifact": Path(path).name,
        "ok": not errors,
        "error": "; ".join(errors) or None,
        "measurements": [result.to_dict() for result in results],
    }
