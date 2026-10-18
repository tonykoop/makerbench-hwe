"""Trusted native expression batching, shared by inspection and public controls."""

import io
import math
import re
import zipfile
import xml.etree.ElementTree as ET

BATCHES = {}


def prepare_batch(doc):
    entries = []
    for sketch in (o for o in doc.Objects if o.TypeId == "Sketcher::SketchObject"):
        expressions = []
        for path, expr in list(sketch.ExpressionEngine):
            clean = path.lstrip(".")
            match = re.fullmatch(r"Constraints(?:\[(\d+)\]|\.(.+))", clean)
            if not match:
                continue
            index = (
                int(match[1])
                if match[1] is not None
                else next(i for i, c in enumerate(sketch.Constraints) if c.Name == match[2])
            )
            expressions.append((index, expr))
            sketch.setExpression(path, None)
        if expressions:
            entries.append((sketch, expressions))
    BATCHES[doc.Name] = entries


def apply_batch(doc):
    # A second pass resolves constraints that refer to another driven datum.
    # Native circular expressions fail initial recompute; never substitute a
    # cached result for a non-convergent expression graph after perturbation.
    entries = BATCHES[doc.Name]
    for _ in range(sum(len(rows) for _, rows in entries) + 1):
        any_changed = False
        for sketch, expressions in BATCHES[doc.Name]:
            # Scalar table expressions (if any) still use FreeCAD's normal engine.
            for obj in doc.Objects:
                if obj != sketch and obj.ExpressionEngine:
                    obj.recompute()
            with zipfile.ZipFile(io.BytesIO(sketch.dumpPropertyContent("Constraints"))) as archive:
                root = ET.fromstring(archive.read("Persistence.xml"))
            nodes = root.findall(".//Constrain")
            changed = False
            constraints = sketch.Constraints
            for index, expr in expressions:
                value = sketch.evalExpression(expr)
                value = value.Value if hasattr(value, "Value") else float(value)
                if constraints[index].Type.startswith("Angle"):
                    value = math.radians(value)
                if not math.isfinite(value):
                    raise ValueError("Nonfinite native constraint expression")
                if float(nodes[index].get("Value")) != value:
                    nodes[index].set("Value", format(value, ".17g"))
                    changed = True
            if changed:
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("Persistence.xml", ET.tostring(root))
                sketch.restorePropertyContent("Constraints", data.getvalue())
                any_changed = True
        if not any_changed:
            return
    raise ValueError("Native constraint expressions did not converge")
