"""Real-kernel mesh-gate parity for the CadQuery arena backend (#754)."""

from __future__ import annotations

from pathlib import Path

import pytest

from makerbench import cadquery_backend
from makerbench import code_cad_arena_runner as arena_runner
from makerbench.code_cad_objective import (
    ObjectiveContext,
    compile_scad_to_artifacts,
)
from makerbench.render import openscad_available


VOLUME_REL_TOLERANCE = 0.005
SPEC = {
    "id": "brep-parity-shell",
    "envelope_mm": [60, 40, 20],
    "min_bodies": 1,
    "min_wall_mm": 2.0,
}


def _context(source: Path, artifacts) -> ObjectiveContext:
    return ObjectiveContext(
        trial_id=source.stem,
        model_id=source.suffix.lstrip("."),
        instrument_id="brep-parity-shell",
        seed=0,
        scad_path=source,
        artifacts=artifacts,
    )


@pytest.mark.skipif(
    not cadquery_backend.cadquery_available() or not openscad_available(),
    reason="real CadQuery and OpenSCAD runtimes are required",
)
def test_cadquery_and_openscad_meshes_receive_identical_objective_verdicts(tmp_path):
    """The same 60x40x20 shell, 2 mm walls, and 10 mm bore has gate parity."""

    scad = tmp_path / "parity.scad"
    scad.write_text(
        """$fn = 128;
difference() {
  cube([60, 40, 20], center=true);
  cube([56, 36, 16], center=true);
  cylinder(h=20, d=10, center=true);
}
""",
        encoding="utf-8",
    )
    cadquery = tmp_path / "parity.py"
    cadquery.write_text(
        """import cadquery as cq
outer = cq.Workplane("XY").box(60, 40, 20)
inner = cq.Workplane("XY").box(56, 36, 16)
bore = cq.Workplane("XY").circle(5).extrude(10, both=True)
result = outer.cut(inner).cut(bore)
""",
        encoding="utf-8",
    )

    scad_artifacts = compile_scad_to_artifacts(scad, tmp_path / "openscad")
    cq_artifacts = cadquery_backend.compile_cadquery_to_artifacts(
        cadquery, tmp_path / "cadquery"
    )
    gate = arena_runner.mesh_objective_gate(SPEC)
    scad_result = gate(_context(scad, scad_artifacts))
    cq_result = gate(_context(cadquery, cq_artifacts))

    assert cq_result["sub_scores"] == scad_result["sub_scores"]
    assert cq_result["passed"] is scad_result["passed"]
    assert cq_result["metrics"]["body_count"] == scad_result["metrics"]["body_count"]
    assert cq_result["metrics"]["bbox_mm"] == pytest.approx(
        scad_result["metrics"]["bbox_mm"], abs=0.02
    )
    assert cq_result["metrics"]["largest_body_volume_mm3"] == pytest.approx(
        scad_result["metrics"]["largest_body_volume_mm3"],
        rel=VOLUME_REL_TOLERANCE,
    )


def test_cadquery_tessellation_tolerances_are_explicit():
    assert cadquery_backend.STL_LINEAR_TOLERANCE_MM == 0.01
    assert cadquery_backend.STL_ANGULAR_TOLERANCE_RAD == 0.1
