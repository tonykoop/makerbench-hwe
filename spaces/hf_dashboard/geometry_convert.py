#!/usr/bin/env python3
"""geometry_convert.py — headless STEP/B-Rep -> glTF hook for the Docker Space (#98).

The issue asks for a Docker Space with "headless geometry libs (pythonocc /
FreeCAD) for STEP/B-Rep inspection". The browser viewer renders ``.glb``/``.stl``
directly, but a submitted ``.step``/``.stp`` B-Rep must be tessellated to a mesh
server-side first. That conversion is the *only* reason the Space needs the heavy
geometry stack in its Dockerfile.

This module isolates that dependency behind one function, ``step_to_glb``. It
imports pythonocc (``OCC.Core``) lazily and returns ``False`` when the library is
absent, so:

  * the data layer and tests stay stdlib-only (no pythonocc in CI), and
  * the running Space degrades honestly — a STEP with no convertible viewer shows
    a download link and a "B-Rep render pending" note instead of crashing.

It reads only the artifact path handed to it (a public submitted B-Rep); it never
touches ``private/oracles/`` or hidden seeds.
"""

from __future__ import annotations

from pathlib import Path

STEP_SUFFIXES = (".step", ".stp")


def pythonocc_available() -> bool:
    """True iff the headless STEP reader is importable in this environment."""
    try:
        import OCC.Core.STEPControl  # noqa: F401
    except Exception:
        return False
    return True


def step_to_glb(src: Path, dst: Path, *, linear_deflection: float = 0.1) -> bool:
    """Tessellate a STEP B-Rep at ``src`` into a binary glTF at ``dst``.

    Returns True on success, False if the headless geometry stack is unavailable
    (the caller then falls back to a download link). Never raises on a missing
    library — only on a genuinely corrupt input once the stack is present.
    """
    src, dst = Path(src), Path(dst)
    if src.suffix.lower() not in STEP_SUFFIXES:
        return False
    if not pythonocc_available():
        return False

    # Imported here so the module stays importable without pythonocc.
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.TopAbs import TopAbs_FACE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopLoc import TopLoc_Location

    reader = STEPControl_Reader()
    if reader.ReadFile(str(src)) != IFSelect_RetDone:
        return False
    reader.TransferRoots()
    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, linear_deflection)

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, location)
        if triangulation is not None:
            offset = len(vertices)
            transform = location.Transformation()
            for i in range(1, triangulation.NbNodes() + 1):
                node = triangulation.Node(i).Transformed(transform)
                vertices.append((node.X(), node.Y(), node.Z()))
            for i in range(1, triangulation.NbTriangles() + 1):
                tri = triangulation.Triangle(i)
                a, b, c = tri.Get()
                faces.append((offset + a - 1, offset + b - 1, offset + c - 1))
        explorer.Next()

    if not faces:
        return False

    # trimesh is already a core makerbench dependency; reuse it for glTF export.
    import numpy as np
    import trimesh

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    dst.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(dst))
    return True
