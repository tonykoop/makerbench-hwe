"""Geometry helper contract tests."""

import numpy as np
import trimesh

from makerbench import geometry as geo


def test_circular_openings_at_z_measures_section_holes():
    plate = trimesh.creation.box(extents=[30, 30, 3])
    plate.apply_translation([15, 15, 1.5])
    for x, y in [(8, 8), (22, 8), (8, 22), (22, 22)]:
        hole = trimesh.creation.cylinder(radius=1.7, height=5, sections=48)
        hole.apply_translation([x, y, 1.5])
        plate = plate.difference(hole)

    openings = geo.circular_openings_at_z(
        plate,
        1.5,
        min_diameter_mm=3.0,
        max_diameter_mm=3.8,
    )

    assert len(openings) == 4
    assert all(abs(opening.diameter_mm - 3.4) < 0.05 for opening in openings)


def test_canonical_sha256_invariant_to_vertex_and_face_reorder():
    """Same geometry, shuffled vertex + face order, must hash identically.

    OpenSCAD/CGAL emit the same mesh with a different vertex emission order
    across runs; the anti-cheat hash must not depend on that ordering.
    """
    base = trimesh.creation.box(extents=[2, 3, 4])
    vertices = np.asarray(base.vertices)
    faces = np.asarray(base.faces)

    rng = np.random.default_rng(0)
    vperm = rng.permutation(len(vertices))
    remap = np.empty_like(vperm)
    remap[vperm] = np.arange(len(vperm))
    fperm = rng.permutation(len(faces))
    shuffled = trimesh.Trimesh(
        vertices=vertices[vperm],
        faces=remap[faces][fperm],
        process=False,
    )

    assert geo.canonical_sha256(shuffled) == geo.canonical_sha256(
        trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    )


def test_canonical_sha256_invariant_to_duplicate_rounded_vertices():
    """A coincident (duplicate) vertex must not change the hash.

    CGAL sometimes emits two vertices that round to the same coordinate; a
    geometry-identity hash must collapse them so equivalent meshes match.
    """
    base = trimesh.creation.box(extents=[2, 3, 4])
    vertices = np.asarray(base.vertices)
    faces = np.asarray(base.faces).copy()

    dup_index = len(vertices)
    vertices_with_dup = np.vstack([vertices, vertices[0]])
    for face in faces:
        members = face.tolist()
        if 0 in members:
            face[members.index(0)] = dup_index
            break
    with_dup = trimesh.Trimesh(vertices=vertices_with_dup, faces=faces, process=False)

    assert geo.canonical_sha256(with_dup) == geo.canonical_sha256(
        trimesh.Trimesh(vertices=vertices, faces=np.asarray(base.faces), process=False)
    )


def test_canonical_sha256_invariant_to_retriangulation():
    """Same surface, different triangle connectivity, must hash the SAME (v2, #151).

    The adopted geometric-summary fingerprint binds surface invariants (volume,
    area, bbox) rather than the face set, so CGAL splitting a coplanar quad on the
    other diagonal across recompiles no longer flips the digest — the
    reproducibility fix from #56 / #151. (Under the old v1 connectivity-aware hash
    these two hashed differently; that test is replaced by this one.)
    """
    quad = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
    )
    diag_02 = trimesh.Trimesh(
        vertices=quad, faces=np.array([[0, 1, 2], [0, 2, 3]]), process=False
    )
    diag_13 = trimesh.Trimesh(
        vertices=quad, faces=np.array([[0, 1, 3], [1, 2, 3]]), process=False
    )

    assert geo.canonical_sha256(diag_02) == geo.canonical_sha256(diag_13)


def test_canonical_sha256_invariant_to_watertight_retriangulation():
    """A box and its convex hull are the same solid, triangulated differently."""
    box = trimesh.creation.box(extents=[2, 3, 4])
    box.apply_translation([1, 1.5, 2])
    hull = trimesh.convex.convex_hull(box)
    assert geo.canonical_sha256(box) == geo.canonical_sha256(hull)


def test_canonical_sha256_separates_solids_sharing_a_vertex_array():
    """Different solids that share a rounded vertex array must NOT collide.

    This is the collision resistance a vertex-set-only hash lacked (the reason it
    was reverted): a cube and a tetrahedron wired from four of the same eight
    corners share the vertex array but enclose different surfaces, so their
    volume/area differ and the v2 digest separates them.
    """
    cube = trimesh.creation.box(extents=[1, 1, 1])
    cube.apply_translation([0.5, 0.5, 0.5])
    corners = np.asarray(cube.vertices)
    tetra = trimesh.Trimesh(
        vertices=corners.copy(),
        faces=np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]),
        process=False,
    )
    assert geo.canonical_sha256(cube) != geo.canonical_sha256(tetra)


def test_canonical_hash_version_marker_is_v2():
    """The fingerprint contract version is exposed for the stored-row marker."""
    assert geo.CANONICAL_HASH_VERSION == 2


def test_centerline_sections_reveal_internal_cavity():
    """A Z mid-section must expose the enclosed cavity an external render hides.

    Builds a closed hollow box (2 mm walls) and checks the centerline Z section
    reports both the outer solid boundary and an interior hole (the cavity), with
    the wall-ring solid area = outer area minus cavity area.
    """
    outer = trimesh.creation.box(extents=[30, 20, 15])
    outer.apply_translation([15, 10, 7.5])
    inner = trimesh.creation.box(extents=[26, 16, 11])  # closed: 2 mm floor/ceiling
    inner.apply_translation([15, 10, 7.5])
    hollow = outer.difference(inner)

    sections = geo.centerline_sections(hollow)
    by_axis = {section.axis: section for section in sections}
    assert set(by_axis) == {"x", "y", "z"}

    z = by_axis["z"]
    assert z.offset_mm == 7.5
    kinds = {loop["kind"] for loop in z.loops}
    assert kinds == {"solid", "hole"}  # cavity revealed by the cut
    # Wall ring = outer 30*20=600 minus cavity 26*16=416 = 184 mm^2.
    assert abs(z.solid_area_mm2 - 184.0) < 1.0
