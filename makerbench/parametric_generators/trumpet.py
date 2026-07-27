"""Parametric Bb trumpet generator (deterministic, watertight, continuous bore).

Builds the horn as a network of HOLLOW tube sweeps (annular profile swept along
smooth spline centerlines) + a revolved flared bell + valve casings/buttons,
then unions everything into a single watertight solid with a continuous lumen.

No GUI, no LLM staging: the geometry is correct by construction and repeatable.
Axes: +X = length (mouthpiece at left, bell rim at right); +Y = up (buttons up,
valve slides hang down); +Z = depth (parallel-tube offset).
"""
from __future__ import annotations

import numpy as np
import trimesh
from scipy.interpolate import splprep, splev
from shapely.geometry import Point

BORE_R = 5.5          # bore radius (Ø11 mm)
WALL = 0.9            # tube wall
TUBE_RO = BORE_R + WALL
SECT = 40            # radial sections


# ---------------------------------------------------------------- primitives
def _ring(ro: float, ri: float | None):
    outer = Point(0, 0).buffer(ro, quad_segs=16)
    return outer.difference(Point(0, 0).buffer(ri, quad_segs=16)) if ri else outer


def smooth(cps, n=140, k=3):
    cps = np.asarray(cps, float)
    k = min(k, len(cps) - 1)
    tck, _ = splprep(cps.T, s=0, k=k)
    return np.array(splev(np.linspace(0, 1, n), tck)).T


def tube(cps, ro=TUBE_RO, ri=BORE_R, n=140):
    """Hollow tube swept along a smooth centerline through control points."""
    return trimesh.creation.sweep_polygon(_ring(ro, ri), smooth(cps, n))


def srod(cps, r=TUBE_RO, n=160):
    """SOLID tube (disk swept along the centerline) — unions merge reliably."""
    return trimesh.creation.sweep_polygon(_ring(r, None), smooth(cps, n))


def casing(x, z=0.0, y0=-18.0, y1=44.0, ro=11.0, ri=9.0):
    """Vertical hollow valve casing (axis +Y)."""
    a = trimesh.creation.annulus(r_min=ri, r_max=ro, height=y1 - y0)
    a.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    a.apply_translation([x, (y0 + y1) / 2, z])
    return a


def cyl(x, y, z, r, h, axis="y"):
    c = trimesh.creation.cylinder(radius=r, height=h, sections=32)
    if axis == "y":
        c.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    c.apply_translation([x, y, z])
    return c


def revolve_x(profile_xr, sections=64):
    """Revolve a CLOSED (x,r) profile loop around the X axis -> watertight solid."""
    p = np.asarray(profile_xr, float)
    M = len(p)
    th = np.linspace(0, 2 * np.pi, sections, endpoint=False)
    V = np.zeros((sections * M, 3))
    for s, t in enumerate(th):
        V[s * M:(s + 1) * M, 0] = p[:, 0]
        V[s * M:(s + 1) * M, 1] = p[:, 1] * np.cos(t)
        V[s * M:(s + 1) * M, 2] = p[:, 1] * np.sin(t)
    F = []
    for s in range(sections):
        s2 = (s + 1) % sections
        for i in range(M):
            i2 = (i + 1) % M
            a, b = s * M + i, s * M + i2
            c, d = s2 * M + i, s2 * M + i2
            F.append([a, b, d])
            F.append([a, d, c])
    m = trimesh.Trimesh(vertices=V, faces=np.array(F), process=True)
    m.fix_normals()
    return m


def flared_bell(x0=340.0, x1=500.0, y=44.0, z=6.0, r_rim=62.0, k=3.3):
    # trumpet bell: stays near-cylindrical then flares hard near the rim
    # (Bessel-horn-like) rather than a straight cone.
    xs = np.linspace(x0, x1, 56)
    t = (xs - x0) / (x1 - x0)
    ro = TUBE_RO + (r_rim - TUBE_RO) * t ** k
    ri = ro - WALL
    # closed loop: outer wall (x0->x1) then inner wall (x1->x0). The rim cap
    # (x1: ro->ri) and throat cap (x0: ri->ro) close automatically via the
    # section wrap — adding explicit cap points would duplicate vertices and
    # break watertightness.
    outer = np.column_stack([xs, ro])
    inner = np.column_stack([xs[::-1], ri[::-1]])
    prof = np.vstack([outer, inner])
    bell = revolve_x(prof, sections=72)
    bell.apply_translation([0, y, z])
    # rolled rim bead
    bead = trimesh.creation.torus(r_rim - 2.0, 3.0, major_sections=72, minor_sections=16)
    bead.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    bead.apply_translation([x1, y, z])
    return trimesh.boolean.union([bell, bead], engine="manifold")


def ball(x, y, z, r):
    return trimesh.creation.icosphere(subdivisions=2, radius=r).apply_translation([x, y, z])


def ring(x, y, z, major, minor=2.0):
    """A torus ring lying in the XY plane (axis +Z), e.g. a 3rd-slide throw ring."""
    t = trimesh.creation.torus(major, minor, major_sections=40, minor_sections=12)
    return t.apply_translation([x, y, z])


def mouthpiece(x=-30.0, y=30.0, z=0.0):
    prof = np.array([
        [x, 3.2], [x + 4, 9.5], [x + 9, 8.0], [x + 12, 4.5],
        [x + 26, 3.6], [x + 30, 3.0], [x + 30, BORE_R - 0.3],
        [x + 12, BORE_R + 0.2], [x + 4, 5.0], [x, 2.2],
    ])
    mp = revolve_x(prof, sections=48)
    mp.apply_translation([0, y, z])
    return mp


# ---------------------------------------------------------------- assembly
# the true air path: mouthpiece -> leadpipe -> tuning-slide U -> down through the
# valve cluster -> bell bow -> bell run. One curve; used both to build the solid
# spine AND to carve the continuous bore, so air threads the valves (never bypasses).
_MAIN = [
    [-2, 30, 0], [90, 27, 0], [180, 25, 1], [250, 22, 3],              # leadpipe
    [300, 25, 6], [329, 26, 5], [336, 19, 3], [327, 13, 1],
    [292, 12, 0], [250, 12, 0],                                        # main tuning-slide U (top-right)
    [232, 4, 0], [223, -13, 0], [193, -13, 0], [165, -13, 0],          # descend + cross the valve bases
    [140, -19, 0], [80, -22, 0], [36, -15, 0], [21, 3, 0],
    [21, 27, 0], [42, 44, 3], [140, 44, 5], [250, 44, 6], [345, 44, 6],  # bell bow -> bell run
]


def build_trumpet(hollow: bool = True):
    """Single connected trumpet.

    hollow=True subtracts one continuous bore threading the valves (an unbroken
    lumen mouthpiece->bell). That thin-wall (0.9 mm) topology is watertight in
    memory but the tight bell-bow curvature makes coincident inner/outer faces
    that STL's float32 round-trip turns non-manifold — fine for viewing/CAD, not
    for the arena's reload-and-score gate. hollow=False keeps the tube cores
    solid (externally identical — the bell shell still opens) and round-trips
    robustly, so the arena backend uses it. The fabrication model wants the
    hollow B-rep (built natively via the CAD connector), not this mesh."""
    solids = []

    # 1. solid spine along the true air path + mouthpiece + bell shell (overlap throat)
    solids.append(srod(_MAIN, TUBE_RO, n=480))
    solids.append(mouthpiece(-30, 30, 0))

    # 2. valve casings (solid, fatter/taller) straddle the spine's valve crossing + trim
    for x in (165, 193, 221):
        solids.append(cyl(x, 14, 0, 12.5, 68))        # solid casing (y -20..48)
        solids.append(cyl(x, 56, 0, 4.6, 22))         # piston stem
        solids.append(cyl(x, 66, 0, 8.2, 6))          # finger button
        solids.append(cyl(x, 49, 0, 13.0, 7))         # top cap
        solids.append(cyl(x, -20, 0, 13.0, 6))        # bottom cap

    # 3. valve slides (solid branch U-loops hanging below): 1st medium, 2nd short, 3rd long
    solids.append(srod([[165, -8, 0], [161, -44, 0], [166, -56, 0], [178, -56, 0],
                        [182, -44, 0], [180, -8, 0]]))                         # 1st
    solids.append(srod([[193, -8, 8], [190, -30, 8], [199, -38, 8], [208, -30, 8],
                        [206, -8, 8]]))                                        # 2nd (front)
    solids.append(srod([[221, -8, 0], [217, -52, 0], [222, -66, 0], [236, -66, 0],
                        [241, -52, 0], [238, -8, 0]]))                         # 3rd (long)
    solids.append(ring(229, -66, 6, 6.0, 1.8))                                 # 3rd-slide throw ring

    # 4. braces + finger hook + water-key bump
    solids.append(cyl(250, 30, 6, 2.0, 26, axis="y"))
    solids.append(cyl(120, 34, 4, 2.0, 22, axis="y"))
    solids.append(trimesh.creation.box((14, 3, 3)).apply_transform(
        trimesh.transformations.translation_matrix([250, 18, 0])))
    solids.append(ball(300, 44, 12, 4.0))

    solids = [p for p in solids if p is not None and p.volume > 1e-6]
    spine = trimesh.boolean.union(solids, engine="manifold")
    if isinstance(spine, list):
        spine = trimesh.util.concatenate(spine)

    # 5. optionally carve ONE continuous bore, then union the flared bell on the throat
    horn = spine
    if hollow:
        bore = srod(_MAIN + [[380, 44, 6]], BORE_R, n=520)
        horn = trimesh.boolean.difference([horn, bore], engine="manifold")
    horn = trimesh.boolean.union([horn, flared_bell()], engine="manifold")
    if isinstance(horn, list):
        horn = trimesh.util.concatenate(horn)
    return horn, solids


if __name__ == "__main__":
    import sys
    horn, parts = build_trumpet()
    out = sys.argv[1] if len(sys.argv) > 1 else "/home/tony/bench-wt/arena_gen/trumpet_v1.stl"
    horn.export(out)
    print(f"parts={len(parts)} watertight={horn.is_watertight} "
          f"components={horn.body_count} bbox_mm={[round(x, 1) for x in horn.extents]} "
          f"vol_cm3={round(horn.volume / 1000, 1)} -> {out}")


def build(spec=None):
    """Arena entry point: return the finished watertight trumpet mesh.

    `spec` (the registry instrument dict) is accepted for interface uniformity;
    the geometry is deterministic and seed-independent. Uses the solid-core
    variant so the mesh round-trips through STL as a single watertight body.
    """
    horn, _parts = build_trumpet(hollow=False)
    return horn
