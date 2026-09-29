# Exploratory: instrument CAD from reference images (Sonnet 5.5, local, no API)

> **EXPLORATORY. Not benchmark data, not an arena entry, not a build model.** One
> local session, one attempt per instrument, no repeat seeds. Nothing here is
> comparable to the sambuca CADAM + Fable 5 pilot (different tool, different
> model, different inputs).

## What was run

Claude Sonnet 5.5, running locally in Claude Code (no OpenRouter, no CADAM, no
metered API), looked at one reference image per instrument, read the public
registry brief (`tasks/code_cad_arena/registry.json`), wrote plain OpenSCAD (no
BOSL2), and rendered it with the local OpenSCAD 2021.01. Each STL was scored with
the unmodified `makerbench.code_cad_arena_runner.mesh_objective_gate` against the
registry spec for that instrument. Parts are separated by 0.2–0.5 mm clearances
so they stay distinct bodies (an assembly-with-clearances, not fused).

**The `.scad`/`.stl` sources are deliberately not committed.** These are solutions
to real registry tasks, and `AGENTS.md` says source geometry belongs in the
private submissions archive, not the public repo. Only previews and scorelines
are here. The sources remain in the session scratchpad and can be handed to the
private archive if Tony wants them kept.

## Reference images: what they actually are

| Instrument | Image used | Kind |
|---|---|---|
| Brian Boru harp | `strings/brian-boru-harp-replica/images/20191125_103813.jpg` (+ 5 siblings, `hero-render.png`) | **Real photos** (Tony, 2019-11-25, museum display case, heavy glare; the repo's own `photo-reference.md` says "Do not scale dimensions from these images") |
| Hammered dulcimer | `strings/hammered-dulcimer/images/hero-render.png` | Appears to be a **rendered/generated concept image** (filename and look); provenance **unknown**, not verified |
| Guzheng | `strings/guzheng/images/hero-render.png` | Same: appears generated; provenance **unknown** |

So only one of three is a real photo, and none of them was measured.

## Where every dimension came from

**No dimension was measured from an image.** Every number is either taken from the
registry brief or is a modelling choice.

*Sourced from the registry brief* (`registry.json`; the brief's own provenance
notes are quoted):

| Instrument | Sourced values | Registry provenance note |
|---|---|---|
| Brian Boru harp | 29 strings; soundboard 715 mm long, 320 → 120 mm wide; box depth 86–95 (modelled at 90); forepillar 790; neck ≈ 457 (target); string band 613 mm; soundholes ⌀19 | "public measurements per Dooley 2012 as recorded in the repo packet — study/reference model only" |
| Hammered dulcimer | 12/11 layout; long rail 914; short rail 190; depth 51; soundboard 3.2; back 3.2; frame 19; course spacing 16.5 | "12/11 diatonic … first-pass design-sheet dims"; tuning is measurement-gated |
| Guzheng | 21 strings; length 1630; width 330; rim 85; crown 30 | **"class-typical study dims only"**: the repo gates all build dimensions behind GUZ-REF-21 (do-not-cut). Treat as class-typical, not as any specific instrument |

*Modelling choices, invented by me, not sourced* (do not quote as measurements):
- Dulcimer: trapezoid height 460 mm; frame ring 19 mm all round (the brief's
  25.4 mm pin-block rails are **not** modelled separately); sound-rose positions
  and diameter (60 mm); bridge positions, section (14 × 26 mm); the 23 course marks
  are placed at an arbitrary pitch of 1.6 × 16.5 mm, not real course positions.
- Guzheng: shell wall/bottom 10 mm; soundboard 8 mm; end blocks 80 mm; two 40 mm
  sound holes; 21 bridge positions, heights (48 → 32 mm) and a diagonal
  "corridor"; bridges hover 0.5 mm above the board.
- Harp: toe angle 34.9° is computed from the three brief lengths (715/790/457);
  box wall 8 mm; 90 mm depth; pillar bow and neck curve are eyeballed from the
  photo, and the neck's straight chord is ≈ 500 mm, **not** the brief's 457 mm
  (a known deviation); two soundholes at 190 and 350 mm along the box, cut
  through both faces; 29 string-band marks are fused to the soundbox, and 29 tuning
  pins are separate display bodies, not drilled holes.

## Objective gate results (this session)

| Instrument | Pass rate | Sub-scores failing | Bodies | Min wall (floor) | bbox mm |
|---|---|---|---|---|---|
| Hammered dulcimer | 1.000 (6/6) | none | 28 | 3.2 (2.0) | 914 × 460 × 77.5 |
| Guzheng | 1.000 (6/6) | none | 25 | 10.0 (2.5) | 1630 × 330 × 165.6 |
| Brian Boru harp | 1.000 (6/6) | none | 32 | 4.8 (2.0) | 567 × 320 × 851 |

Passing means only that the mesh renders, is watertight, fits the envelope, has
enough distinct bodies and no wall under the floor. It says nothing about
fidelity to the instrument, and the gate cannot see the deviations listed above.

### What the failed first attempts showed

- **Guzheng, first run: 5/6.** The bridges sat on the underside of the soundboard
  and fused into it, so the gate reported 4 bodies and a min wall of 0.08 mm.
  That was a real modelling bug, not gate noise. Fixed by seating each bridge on
  the highest edge of its footprint.
- **Harp, first run: 5/6** (min wall 0.016 mm). Cause found by scoring each body
  separately: the neck, where I had drilled 29 ⌀5 mm holes. The soundbox itself
  measured 2.9–4.8 mm. Replacing the holes with separate pin bodies fixed it.
- The wall estimator is seed-dependent (the same mesh read 0.016–0.055 mm across
  seeds), so a single low reading is a flag to investigate, not a measurement.

Only the final versions are scored above. The two failures are reported because
the fixes were made after seeing the gate output (the gate was not modified).

## Honest limits

- n = 1 per instrument, one model, one session; no ranking claim of any kind.
- The dulcimer and guzheng references may be AI-generated, so "from photos"
  holds only for the harp, and even there dimensions came from the text brief.
- Shape fidelity is crude by eye: the harp reads as a triangle of a box and two
  rods; the dulcimer is a slab with two bars; the guzheng is an arched box with a
  row of small bridges. Previews are in `assets/`.
- The `min_wall` floors are provisional per `docs/CODE_CAD_ARENA_ROUND1.md`.
- Cost: no API spend. Wall-clock and token usage were not recorded.
