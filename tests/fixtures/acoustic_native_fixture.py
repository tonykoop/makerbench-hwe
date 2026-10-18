"""Generated analytical control, executed inside the FreeCAD geometry sandbox."""

import sys
import FreeCAD as App
import Part

variant = sys.argv[1] if len(sys.argv) > 1 else "through"
height = 8 if variant == "thin" else 12
plate = Part.makeBox(220, 120, height)
z = 4 if variant == "blind" else -1
cut_height = 20
for x0, x1, y0, y1 in [
    (20, 100, 28, 30),
    (20, 100, 40, 42),
    (95, 100, 28, 42),
    (120, 200, 78, 80),
    (120, 200, 90, 92),
    (120, 125, 78, 92),
]:
    plate = plate.cut(Part.makeBox(x1 - x0, y1 - y0, cut_height, App.Vector(x0, y0, z)))
plate = plate.removeSplitter()
plate.exportBrep("/out/panel.brep")
