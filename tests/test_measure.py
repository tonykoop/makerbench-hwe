"""Golden-fixture tests for the compiled-candidate measure helpers (#794).

Fixtures are generated in the test, with analytically known answers. Each
estimator is held to the tolerance its module states, so a method change that
drifts past it fails here.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
import trimesh

from makerbench import measure


def _tube(r_inner: float = 8.0, r_outer: float = 10.0, height: float = 30.0) -> trimesh.Trimesh:
    return trimesh.creation.annulus(r_min=r_inner, r_max=r_outer, height=height, sections=256)


def _two_cubes(gap: float) -> dict[str, trimesh.Trimesh]:
    left = trimesh.creation.box(extents=[10, 10, 10])
    right = trimesh.creation.box(extents=[10, 10, 10])
    right.apply_translation([10.0 + gap, 0.0, 0.0])
    return {"left": left, "right": right}


def _wall_tolerance(expected: float) -> float:
    return max(measure.WALL_TOLERANCE_MM, measure.WALL_TOLERANCE_REL * expected)


@pytest.mark.parametrize("wall", [2.0, 1.2, 3.5])
def test_tube_min_wall_is_within_stated_tolerance(wall):
    result = measure.min_wall_thickness(_tube(r_inner=10.0 - wall, r_outer=10.0))

    assert result.ok, result.error
    assert result.unit == "mm"
    assert abs(result.value - wall) <= _wall_tolerance(wall)
    assert "inward ray sampling" in result.method
    assert result.details == {"samples": measure.DEFAULT_WALL_SAMPLES, "seed": 0}


def test_min_wall_is_deterministic_for_a_seed():
    tube = _tube()
    assert measure.min_wall_thickness(tube, seed=7) == measure.min_wall_thickness(tube, seed=7)


def test_min_wall_finds_the_thin_side_of_an_uneven_shell():
    outer = trimesh.creation.box(extents=[20, 20, 20])
    inner = trimesh.creation.box(extents=[14, 17, 14])
    inner.apply_translation([0.0, 0.5, 0.0])  # y walls become 1.0 and 2.0
    shell = outer.difference(inner)

    result = measure.min_wall_thickness(shell)

    assert result.ok, result.error
    assert abs(result.value - 1.0) <= _wall_tolerance(1.0)


@pytest.mark.parametrize("gap", [0.5, 2.0, 7.25])
def test_clearance_between_two_cubes_matches_the_gap(gap):
    result = measure.clearance(_two_cubes(gap), "left", "right")

    assert result.ok, result.error
    assert result.details["interfering"] is False
    assert abs(result.value - gap) <= measure.CLEARANCE_TOLERANCE_MM


def test_clearance_vertices_alone_resolve_a_corner_to_face_gap():
    # A cube rotated so one corner points at the other cube's face: only a
    # vertex sits at the true minimum, so surface samples alone would overshoot.
    left = trimesh.creation.box(extents=[10, 10, 10])
    corner = trimesh.creation.box(extents=[10, 10, 10])
    corner.apply_transform(trimesh.transformations.rotation_matrix(math.radians(45), [0, 0, 1]))
    corner.apply_transform(
        trimesh.transformations.rotation_matrix(math.atan(1 / math.sqrt(2)), [0, 1, 0])
    )
    corner.apply_translation([5.0 + 1.0 - corner.bounds[0][0], 0.0, 0.0])
    tip = corner.vertices[np.argmin(corner.vertices[:, 0])]
    corner.apply_translation([0.0, -tip[1], -tip[2]])
    bodies = {"left": left, "corner": corner}

    result = measure.clearance(bodies, "left", "corner", samples=0)

    assert result.ok, result.error
    assert abs(result.value - 1.0) <= measure.CLEARANCE_TOLERANCE_MM


def test_clearance_is_symmetric_in_body_order():
    bodies = _two_cubes(1.5)
    ab = measure.clearance(bodies, "left", "right").value
    ba = measure.clearance(bodies, "right", "left").value
    assert ab == pytest.approx(ba, abs=measure.CLEARANCE_TOLERANCE_MM)


def test_overlapping_bodies_report_interference_not_a_negative_gap():
    result = measure.clearance(_two_cubes(-2.0), "left", "right")

    assert result.ok
    assert result.value == 0.0
    assert result.details["interfering"] is True
    assert result.details["overlap_mm3"] == pytest.approx(200.0, rel=1e-6)


def test_body_nested_inside_another_is_interfering():
    outer = trimesh.creation.box(extents=[20, 20, 20])
    inner = trimesh.creation.box(extents=[2, 2, 2])
    result = measure.clearance({"outer": outer, "inner": inner}, "outer", "inner")
    assert result.ok and result.details["interfering"] is True


def test_clearance_unknown_or_duplicate_body_is_an_error():
    bodies = _two_cubes(1.0)
    missing = measure.clearance(bodies, "left", "lid")
    assert not missing.ok and missing.value is None
    assert "unknown body 'lid'" in missing.error
    assert missing.details["available"] == ["left", "right"]
    same = measure.clearance(bodies, "left", "left")
    assert not same.ok and same.value is None


def test_split_bodies_names_components_by_position():
    bodies = _two_cubes(3.0)
    merged = trimesh.util.concatenate([bodies["right"], bodies["left"]])

    named = measure.split_bodies(merged)

    assert sorted(named) == ["body_0", "body_1"]
    assert named["body_0"].bounds[0][0] < named["body_1"].bounds[0][0]
    assert measure.clearance(named, "body_0", "body_1").value == pytest.approx(3.0, abs=1e-6)


def test_load_bodies_splits_a_merged_stl(tmp_path):
    bodies = _two_cubes(4.0)
    path = tmp_path / "assembly.stl"
    trimesh.util.concatenate(list(bodies.values())).export(path)

    named = measure.load_bodies(path)

    assert measure.clearance(named, "body_0", "body_1").value == pytest.approx(4.0, abs=1e-5)


def test_load_bodies_applies_scene_node_transforms(tmp_path):
    # Placement lives in the scene graph, not in the geometry's local vertices:
    # two identical 10 mm cubes, nodes 20 mm apart, so the true gap is 10 mm.
    cube = trimesh.creation.box(extents=[10, 10, 10])
    scene = trimesh.Scene()
    scene.add_geometry(cube, node_name="body", geom_name="cube",
                       transform=trimesh.transformations.translation_matrix([0, 0, 0]))
    scene.add_geometry(cube, node_name="lid", geom_name="cube",
                       transform=trimesh.transformations.translation_matrix([20, 0, 0]))
    path = tmp_path / "assembly.glb"
    scene.export(path)

    named = measure.load_bodies(path)

    assert sorted(named) == ["body", "lid"]
    assert named["lid"].bounds[0] == pytest.approx([15, -5, -5])
    result = measure.clearance(named, "body", "lid")
    assert result.ok and result.details["interfering"] is False
    assert result.value == pytest.approx(10.0, abs=1e-5)


def test_sphere_section_area_matches_disc():
    radius = 10.0
    sphere = trimesh.creation.icosphere(subdivisions=5, radius=radius)
    for offset in (0.0, 6.0):
        result = measure.section_area(sphere, axis="z", offset_mm=offset)
        expected = math.pi * (radius**2 - offset**2)
        assert result.ok, result.error
        assert result.details["intersects"] is True
        assert abs(result.value - expected) / expected <= measure.SECTION_AREA_TOLERANCE_REL


def test_arbitrary_plane_section_through_sphere_centre():
    sphere = trimesh.creation.icosphere(subdivisions=5, radius=10.0)
    result = measure.section_area(sphere, origin=[0, 0, 0], normal=[1, 1, 1])
    assert result.ok
    assert abs(result.value - math.pi * 100) / (math.pi * 100) <= measure.SECTION_AREA_TOLERANCE_REL
    assert result.details["plane"]["normal"] == pytest.approx([1 / math.sqrt(3)] * 3)


def test_tube_section_subtracts_the_bore():
    result = measure.section_area(_tube(), axis="z", offset_mm=0.0)
    expected = math.pi * (10.0**2 - 8.0**2)
    assert abs(result.value - expected) / expected <= measure.SECTION_AREA_TOLERANCE_REL


def test_plane_missing_the_solid_is_a_true_zero():
    result = measure.section_area(trimesh.creation.box(extents=[2, 2, 2]), axis="x", offset_mm=50.0)
    assert result.ok and result.value == 0.0
    assert result.details["intersects"] is False


def test_bad_plane_arguments_are_errors():
    box = trimesh.creation.box(extents=[2, 2, 2])
    assert "axis must be" in measure.section_area(box, axis="w").error
    assert "non-zero" in measure.section_area(box, origin=[0, 0, 0], normal=[0, 0, 0]).error
    assert "either axis" in measure.section_area(box).error


def test_volume_and_bbox_of_a_box():
    box = trimesh.creation.box(extents=[10, 20, 30])
    box.apply_translation([5, 10, 15])

    vol = measure.volume(box)
    bb = measure.bbox(box)

    assert vol.ok and vol.value == pytest.approx(6000.0)
    assert bb.ok and bb.value == pytest.approx([10, 20, 30])
    assert bb.details["min"] == pytest.approx([0, 0, 0])
    assert bb.details["max"] == pytest.approx([10, 20, 30])


def _open_mesh() -> trimesh.Trimesh:
    box = trimesh.creation.box(extents=[10, 10, 10])
    return trimesh.Trimesh(vertices=box.vertices, faces=box.faces[:-2], process=False)


def _bowtie() -> trimesh.Trimesh:
    """Two closed tetrahedra glued along one shared edge (edge used 4 times)."""
    v = np.array([[0, 0, 0], [0, 0, 1], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], float)
    tet_a = [[0, 2, 1], [0, 3, 2], [0, 1, 3], [1, 2, 3]]
    tet_b = [[0, 1, 4], [0, 4, 5], [0, 5, 1], [1, 5, 4]]
    return trimesh.Trimesh(vertices=v, faces=tet_a + tet_b, process=False)


@pytest.mark.parametrize(
    "mesh, needle",
    [
        (trimesh.Trimesh(), "empty mesh"),
        (_open_mesh(), "non-manifold mesh: 4 boundary"),
        (_bowtie(), "shared by more than two faces"),
    ],
    ids=["empty", "open", "bowtie"],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda m: measure.volume(m),
        lambda m: measure.min_wall_thickness(m),
        lambda m: measure.section_area(m, axis="z", offset_mm=0.1),
        lambda m: measure.clearance({"a": m, "b": trimesh.creation.box()}, "a", "b"),
    ],
    ids=["volume", "min_wall", "section", "clearance"],
)
def test_invalid_meshes_give_an_error_never_a_number(mesh, needle, call):
    result = call(mesh)
    assert result.ok is False
    assert result.value is None
    assert needle in result.error


def test_inside_out_mesh_is_an_error():
    box = trimesh.creation.box(extents=[4, 4, 4])
    box.invert()
    result = measure.min_wall_thickness(box)
    assert not result.ok and result.value is None
    assert "inside-out" in result.error


def test_min_wall_with_no_ray_hit_is_an_error_not_infinity(monkeypatch):
    monkeypatch.setattr(measure.geometry, "estimate_min_wall_mm", lambda *a, **k: float("inf"))
    result = measure.min_wall_thickness(_tube())
    assert not result.ok and result.value is None
    assert "no inward ray hit" in result.error


def test_empty_bbox_is_an_error():
    result = measure.bbox(trimesh.Trimesh())
    assert not result.ok and result.value is None


def test_measure_candidate_report_is_json_and_flags_load_errors(tmp_path):
    path = tmp_path / "output.stl"
    _tube().export(path)

    report = measure.measure_candidate(path, sections=[{"axis": "z", "offset_mm": 0.0}])

    assert report["ok"] is True
    metrics = [m["metric"] for m in report["measurements"]]
    assert metrics == ["volume", "bbox", "min_wall_thickness", "section_area"]
    json.dumps(report)

    empty = tmp_path / "empty.stl"
    empty.write_bytes(b"")
    bad = measure.measure_candidate(empty)
    assert bad["ok"] is False and bad["error"]


def test_measure_candidate_malformed_section_spec_is_an_error_entry(tmp_path):
    path = tmp_path / "output.stl"
    _tube().export(path)

    report = measure.measure_candidate(path, sections=[{"axis": "z", "offst_mm": 1.0}])

    assert report["ok"] is False
    (section,) = [m for m in report["measurements"] if m["metric"] == "section_area"]
    assert section["value"] is None and "invalid section spec" in section["error"]


def test_step_mesh_matches_the_brep(tmp_path):
    pytest.importorskip("OCP", reason="STEP measurement needs the optional CadQuery/OCP extra")
    cq = pytest.importorskip("cadquery")
    tube = cq.Workplane("XY").circle(10).circle(8).extrude(30)
    path = tmp_path / "tube.step"
    cq.exporters.export(tube, path.as_posix())

    mesh = measure.load_mesh(path)

    assert measure.mesh_problem(mesh) is None
    assert measure.volume(mesh).value == pytest.approx(math.pi * 36 * 30, rel=0.01)
    wall = measure.min_wall_thickness(mesh)
    assert abs(wall.value - 2.0) <= _wall_tolerance(2.0)


def test_step_without_ocp_is_an_explicit_load_error(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "ocp_available", lambda: False)
    path = tmp_path / "part.step"
    path.write_text("ISO-10303-21;", encoding="utf-8")
    report = measure.measure_candidate(path)
    assert report["ok"] is False
    assert "needs the optional OCP kernel" in report["error"]
