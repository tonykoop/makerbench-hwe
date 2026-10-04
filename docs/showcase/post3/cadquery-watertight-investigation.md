# CadQuery `watertight` failures in the ocarina backend matchup: gate artifact or real geometry?

Story #874 (epic #873). Objective evidence only; no votes, no Elo, $0 (re-analysis of the
three existing meshes, no model calls). Source run: the CadQuery arm of the backend matchup
(`runs/code_cad_arena/s3-847-cadquery`, ocarina, seeds 0-2, `claude-code-sonnet-5.5`).

## Answer

Two of the three failures were a **gate artifact**; one is **real geometry**.

| Seed | Before (as published) | Cause | After the gate fix |
|---|---|---|---|
| 0 | `watertight` fail | 4 zero-area sliver triangles in the STL | passes all checks |
| 1 | `watertight` fail | same 4 slivers | passes all checks |
| 2 | `watertight` fail, `min_wall` fail | same 4 slivers **plus** a real non-manifold edge pair in the design | still `watertight` and `min_wall` fail (correctly) |

CadQuery objective pass rate on these exact meshes: 0.778 before, 0.889 after (16 of 18
sub-scores). The build123d meshes show the same slivers (3-4 per mesh in the two that
finished), so its `watertight` failures in that matchup were the same artifact; that re-run is #876.

## Evidence

1. **Export path.** The STEP is fine: each of the three has one `MANIFOLD_SOLID_BREP` in
   one `CLOSED_SHELL`. The STL comes from OCC's writer at 0.01 mm / 0.1 rad
   (`makerbench/cadquery_backend.py`).
2. **Multi-solid handling.** Loaded with trimesh, each STL is one large body (8.7k faces,
   watertight in seeds 0 and 1) plus **four one-face "bodies"** at the ellipsoid seam/pole
   points (`z = ±25.3` and `±21.3` in seed 0). Each has zero area and zero volume. The gate
   calls `mesh.split()`, which turns every sliver into its own body; a one-triangle body is
   never watertight, so the `watertight` check ("every body manifold") failed, and
   `body_count` read 5 instead of 1.
3. **Tessellation tolerance is not the cause.** With the slivers removed, seeds 0 and 1 are
   watertight, one body, volumes unchanged (61,982 and 66,229 mm³). Vertex-merge tolerance
   changes do not affect the result.
4. **Seed 2 is a genuine design defect.** After removing the slivers the mesh still has two
   edges shared by more than two faces, at `x = 57, z = 3.5, y = ±(4.5 to 6)`. The entrant
   script cuts a windway duct (`z 3.5-6`) and a void (`z -2-3.5`) that meet the voicing
   window only along an edge, leaving a non-manifold junction. That is the model's geometry,
   and the gate is right to fail it.

## Fix

`mesh_objective_gate` (`makerbench/code_cad_arena_runner.py`) now drops zero-area triangles
(height below 1e-6 mm) before splitting into bodies, and reports `degenerate_faces_dropped`
in the row metrics. They carry no area or volume, so no genuine geometry is affected. The
change is also a correctness fix for `body_count`: slivers can no longer satisfy a
`min_bodies` assembly floor. Tests cover slivers on a watertight solid, slivers not
counting as bodies, and the metric.

## Not established

- Whether OpenSCAD meshes ever carry such slivers (it did not in this matchup).
- The gate's `split()` also silently fills small holes (trimesh `repair=True` default), so a
  single missing triangle is not caught. Noticed while testing; out of scope here.
- One task, three seeds, one model: this is about the tooling, not a comparison of CAD systems.
