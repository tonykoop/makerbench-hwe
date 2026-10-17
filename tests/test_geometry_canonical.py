"""Geometry the arena gate still relies on after wall thickness left scoring (2026-10-16).

Kept from the retired wall-estimator tests: the order-independent canonical mesh and tie-break key,
the bounded-batch wall ray cast shared with the DFM tasks, the unchanged seeded minimum, and the
gate's order-independent choice of its largest body.
"""
from __future__ import annotations

import numpy as np
import pytest
import trimesh

from makerbench import code_cad_arena_runner as runner
from makerbench import geometry
from makerbench.code_cad_objective import ObjectiveContext, RenderArtifacts

SPEC = {"id": "x", "envelope_mm": [200, 200, 200], "min_bodies": 1}


@pytest.fixture(scope="module")
def plate_with_a_tiny_blade() -> trimesh.Trimesh:
    """A 50x50x5 plate with a 1 mm-wide, 0.3 mm-thick blade sticking out of one side."""
    plate = trimesh.creation.box(extents=[50, 50, 5])
    blade = trimesh.creation.box(extents=[1, 1, 0.3])
    blade.apply_translation([25, 0, 0])
    mesh = trimesh.boolean.union([plate, blade], engine="manifold")
    assert mesh.is_watertight and len(mesh.split(only_watertight=False)) == 1
    return mesh


def _payload(tmp_path, mesh, **kw):
    stl = tmp_path / "output.stl"
    mesh.export(stl.as_posix())
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n")
    ctx = ObjectiveContext(trial_id="t", model_id="m", instrument_id="i", seed=0,
                           scad_path=tmp_path / "x.scad", artifacts=RenderArtifacts(stl_path=stl, png_path=png))
    spec = kw.pop("spec", SPEC)
    return runner.mesh_objective_gate(spec, **kw)(ctx)


def test_estimate_min_wall_mm_is_unchanged_by_sharing_the_sampling_code(plate_with_a_tiny_blade):
    # Values recorded from the estimator BEFORE the sampling code was factored out (4000 samples,
    # seeds 0-9), so the default behaviour, and every committed result, is provably the same.
    got = [round(geometry.estimate_min_wall_mm(plate_with_a_tiny_blade, 4000, seed=s), 2) for s in range(10)]
    assert got == [5.0, 5.0, 1.0, 0.3, 5.0, 0.3, 5.0, 5.0, 5.0, 5.0]


@pytest.mark.parametrize("samples, seed", [(4000, 0), (4000, 7), (20000, 0)])
def test_batched_wall_ray_cast_is_identical_to_one_cast(monkeypatch, samples, seed):
    """#997: wall rays are cast in batches (a single 20,000-ray robust-v1 cast on a large
    mesh peaked at ~18 GB and killed CI runners). Batching must not change a single value
    or the order the hits come back in (min_wall_sample's argmin depends on it)."""
    tube = trimesh.creation.annulus(r_min=9, r_max=12, height=400, sections=96)
    monkeypatch.setattr(geometry, "WALL_RAY_BATCH", 10**9)
    whole = geometry._wall_samples(tube, samples, seed)
    monkeypatch.setattr(geometry, "WALL_RAY_BATCH", 997)  # uneven batches
    batched = geometry._wall_samples(tube, samples, seed)
    for one, many in zip(whole, batched):
        assert np.array_equal(one, many)


def _holed_tube() -> trimesh.Trimesh:
    """The topology-gate flute: a tube with tone holes cut by (threaded) manifold booleans."""
    tube = trimesh.creation.annulus(r_min=8, r_max=10, height=100, sections=96)
    for z in (30.0, 50.0, 70.0):
        hole = trimesh.creation.cylinder(radius=2.5, height=6, sections=48)
        hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        hole.apply_translation([10.0, 0.0, z])
        tube = tube.difference(hole)
    return tube


def _reordered(mesh: trimesh.Trimesh, seed: int) -> trimesh.Trimesh:
    """Same surface, shuffled vertex order, face order and per-face corner rotation
    (winding kept)."""
    rng = np.random.default_rng(seed)
    vperm = rng.permutation(len(mesh.vertices))
    inverse = np.empty_like(vperm)
    inverse[vperm] = np.arange(len(vperm))
    faces = inverse[mesh.faces][rng.permutation(len(mesh.faces))]
    shift = rng.integers(3, size=len(faces))
    faces = np.take_along_axis(faces, (shift[:, None] + np.arange(3)) % 3, axis=1)
    return trimesh.Trimesh(vertices=mesh.vertices[vperm], faces=faces, process=False)


def test_canonical_mesh_is_order_independent_and_keeps_the_surface():
    mesh = _holed_tube()
    canon = geometry.canonical_mesh(mesh)
    for seed in range(6):
        other = geometry.canonical_mesh(_reordered(mesh, seed))
        assert np.array_equal(other.vertices, canon.vertices)
        assert np.array_equal(other.faces, canon.faces)
    assert canon.is_watertight
    assert canon.volume == pytest.approx(mesh.volume, rel=1e-12)
    # winding (so every face normal) is unchanged: same set of (centroid, normal) pairs
    key = lambda m: np.round(np.hstack([m.triangles_center, m.face_normals]), 9)  # noqa: E731
    assert np.array_equal(np.unique(key(canon), axis=0), np.unique(key(mesh), axis=0))


def _two_boxes(first_thin: bool) -> trimesh.Trimesh:
    """Two disjoint watertight 12-face boxes (a 0.4 mm plate and a 5 mm cube), listed in
    either order: a face-count tie between bodies."""
    thin = trimesh.creation.box(extents=[20.0, 20.0, 0.4])
    cube = trimesh.creation.box(extents=[5.0, 5.0, 5.0])
    cube.apply_translation([40.0, 0.0, 0.0])
    pair = [thin, cube] if first_thin else [cube, thin]
    return trimesh.util.concatenate(pair)


def test_canonical_rotation_is_unique_for_degenerate_faces():
    faces = np.array([[2, 0, 0], [0, 2, 0], [0, 0, 2], [1, 3, 1], [3, 1, 1], [1, 1, 3]])
    rotated = geometry._smallest_rotation(faces)
    assert rotated.tolist() == [[0, 0, 2]] * 3 + [[1, 1, 3]] * 3
    verts = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    one = geometry.canonical_mesh(trimesh.Trimesh(verts, faces[[0, 3]], process=False))
    two = geometry.canonical_mesh(trimesh.Trimesh(verts, faces[[4, 2]], process=False))
    assert np.array_equal(one.faces, two.faces) and np.array_equal(one.vertices, two.vertices)


def test_tie_break_key_is_computed_from_the_canonical_mesh():
    """#1008 review: the tie-break volume must come from the canonical mesh. A raw
    ``mesh.volume`` sums in face order, and far from the origin that noise survives the
    9-decimal rounding (160.0 vs 160.000000001 vs 160.000000002 across shuffles here), so
    it, not geometry, would decide between equal-volume bodies."""
    body = trimesh.creation.box(extents=[20.0, 20.0, 0.4])
    body.apply_transform(trimesh.transformations.euler_matrix(0.3, 0.7, 1.1))
    body.apply_translation([3.0e4, 3.0e4, 3.0e4])
    raw = {round(abs(float(_reordered(body, s).volume)), 9) for s in range(8)}
    assert len(raw) > 1  # the hazard is real for raw volumes
    keys = {geometry.canonical_order_key(_reordered(body, s)) for s in range(8)}
    assert len(keys) == 1


# --- the largest body behind nonzero_volume / largest_body_volume_mm3 (#1009) -----------------

def test_largest_body_is_chosen_independently_of_listing_order(tmp_path):
    """Two equally large (12-face) bodies listed in either order: the gate reports the same
    largest_body_volume_mm3 and nonzero_volume (the 20 x 20 x 0.4 plate, 160 mm3, not the cube)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _payload(tmp_path / "a", _two_boxes(first_thin=True))
    b = _payload(tmp_path / "b", _two_boxes(first_thin=False))
    assert a["metrics"]["largest_body_volume_mm3"] == b["metrics"]["largest_body_volume_mm3"] == pytest.approx(160.0)
    assert a["sub_scores"]["nonzero_volume"] == b["sub_scores"]["nonzero_volume"]


def test_largest_body_tie_can_no_longer_flip_nonzero_volume(tmp_path):
    """A degenerate tie: a speck below MIN_BODY_VOLUME_MM3 and a 12 mm cube, both 12 faces. The
    gate picks the cube whichever is listed first."""
    speck = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
    cube = trimesh.creation.box(extents=[12.0, 12.0, 12.0])
    cube.apply_translation([40.0, 0.0, 0.0])
    results = {}
    for name, pair in (("speck_first", [speck, cube]), ("cube_first", [cube, speck])):
        (tmp_path / name).mkdir()
        results[name] = _payload(tmp_path / name, trimesh.util.concatenate(pair))
    assert results["speck_first"]["sub_scores"]["nonzero_volume"] == results["cube_first"]["sub_scores"]["nonzero_volume"] == 1.0
    assert results["speck_first"]["metrics"]["largest_body_volume_mm3"] == pytest.approx(1728.0)


def test_gate_scores_exactly_the_arena_checks(tmp_path):
    out = _payload(tmp_path, trimesh.creation.box(extents=[10, 10, 10]))
    assert tuple(out["sub_scores"]) == runner.ARENA_GATE_CHECKS
    assert out["objective_pass_rate"] == 1.0
    text = str(out)
    assert "min_wall" not in text and "borderline" not in text
