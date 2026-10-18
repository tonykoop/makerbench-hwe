"""BREP-only inspection in the existing no-network FreeCAD sandbox; no FCStd execution."""

import json
from pathlib import Path
import FreeCAD as App
import Part

shape = Part.Shape()
shape.read("/inputs/panel.brep")
if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
    raise ValueError("Require one valid plate solid")
box = shape.BoundBox
bottom = [
    f
    for f in shape.Faces
    if isinstance(f.Surface, Part.Plane)
    and f.BoundBox.ZLength < 1e-7
    and abs(f.BoundBox.ZMin - box.ZMin) < 1e-7
]
if len(bottom) != 1 or abs(box.ZLength - 12) > 1e-5:
    raise ValueError("Require one planar XY bottom and measured thickness 12 mm")
face = bottom[0]
extrusion = face.extrude(App.Vector(0, 0, box.ZLength))
if shape.cut(extrusion).Volume + extrusion.cut(shape).Volume > 1e-5:
    raise ValueError("Require a uniform through-cut XY plate; blind kerfs are unsupported")


def ring(wire):
    points = wire.discretize(Deflection=0.02)
    return [[p.x, p.y] for p in points]


result = dict(
    outer=ring(face.OuterWire),
    holes=[ring(w) for w in face.Wires if not w.isSame(face.OuterWire)],
    thickness_mm=box.ZLength,
    deflection_mm=0.02,
    freecad_version=App.Version(),
    bounds_mm=[box.XMin, box.YMin, box.XMax, box.YMax],
)
Path("/out/footprint.json").write_text(json.dumps(result))
