# Strings matchup: does a reference image help? (context tier varied)

Story #881 (epic #879). A real run with the subscription `claude` CLI: $0 metered, no
pay-per-token entrant. Instrument: `sambuca` (boat-shaped harp; public repo
`tonykoop/sambuca`; inventory in `README.md`).

## Result (objective checks only)

Entrant held: Claude Sonnet 5.5. Backend held: OpenSCAD. Varied: context tier, `blind`
(written brief only) versus `image` (brief plus a reference image). Seeds 0, 1, 2.

| Context tier | Seed 0 | Seed 1 | Seed 2 | Mean pass rate | What failed |
|---|---|---|---|---|---|
| blind | 1.000 | 0.833 | 0.833 | 0.889 | `min_wall` in seeds 1 and 2 |
| image | 0.833 | 0.833 | 0.833 | 0.833 | `min_wall` in all three |

**The reference image did not help on the objective checks here, and the difference
(0.889 vs 0.833, one seed) is within what three seeds cannot separate.** The honest line:
no measurable benefit on this gate, with a small sample. The gate does not check
resemblance to the reference, so it cannot say whether the image made the design look
more like the instrument.

By eye, across all six committed renders (three seeds per tier), the designs differ in
kind: the three blind designs are arched-neck harps (`blind-seed0/1/2.png`), while the
three image designs are upright-pillar harps with sloping strings (`image-seed0/1/2.png`).
This is a visual observation, not a score, and I did not compare either family against the
source photo side by side in this report. It comes from three seeds per tier, so it is a
pattern in this small sample, not a general claim about image context.

## Wall time

One `arena run` per (tier, seed), timed with `date` around the command; scope is the
whole process (start-up, CLI generation, compile, render, gate), not model-thinking time.
Raw values (rounded elapsed seconds, author-recorded; original start/end stamps were not kept, so they cannot be independently re-verified) are in `matchup-context/wall-time.tsv`.

| Tier | Seed 0 | Seed 1 | Seed 2 | Mean |
|---|---|---|---|---|
| blind | 193.9 s | 156.0 s | 132.0 s | 160.6 s |
| image | 142.4 s | 119.2 s | 126.1 s | 129.2 s |

Six samples, provider-side load unknown; do not read the difference as image being faster.

## What was held and varied

`matchup-context/preview.json` is the `arena matchup --vary context` preview (varied
axis `context_tiers`; held: instrument `sambuca`, model `claude-code-sonnet-5.5`,
level L1, seed 0, backend `openscad`; cost source `subscription_zero_marginal`). Seeds 1
and 2 were run as repeats.

## Commands

```bash
python -m makerbench.cli arena matchup --vary context --values blind,image \
  --instruments sambuca --models claude-code-sonnet-5.5 --out preview.json

for tier in blind image; do for seed in 0 1 2; do
  xvfb-run -a python -m makerbench.cli arena run \
    --run-dir runs/code_cad_arena/s6-881-$tier-seed$seed \
    --instruments sambuca --models claude-code-sonnet-5.5 --seeds $seed \
    --context-tier $tier [--image-map imagemap.json] --backend openscad \
    --model-map modelmap.json
done; done
```

`modelmap.json` maps `claude-code-sonnet-5.5` to provider `claude`, model
`claude-sonnet-5-5` (see `../post3/matchup-model.md`). `imagemap.json` maps `sambuca` to a
local copy of the reference photo.

## Provenance and licence

- Code: `origin/main` at `0d84432`, Claude Code CLI 2.1.285, 2026-09-30.
- **The reference photo is a third-party museum photograph whose licence is unresolved**
  (see `../sambuca/CASE_STUDY.md` section 3). It was used only as a local model input and is
  **not committed**. The committed PNGs are OpenSCAD renders of the models' own
  designs; because they were produced with the photo as input in the image tier,
  reuse of the `image-seed*.png` renders is gated on the same unresolved licence question.
  The `blind-seed*.png` renders had no photo input.
- Run directories, scripts and STL stay in the gitignored `runs/`. Turn counts and token
  usage were not recorded. No preference votes or Elo are involved.


## Re-scored under the gate now (#904)

The table at the top of this page is unchanged and is the published result. This section
re-scores the same six committed designs **with no regeneration** (each design's `output.stl`
and SCAD from the original run directories, read-only) so the two can be read side by side. It is
a replay of the objective gate, not a new experiment: no model was called.

Three readings per design:

- **Recorded**: the pass rate published above (gate as it was when the run was made).
- **Current gate**: today's default gate (min_wall as the minimum over 4,000 random samples,
  seed 0), including the zero-area-sliver handling fixed in #874/#922, and the failed-check
  explanation from #903 (the measured min_wall is shown).
- **Robust (opt-in)**: the `robust-v1` option from #901 (1st percentile over 20,000 samples,
  fixed seed), shown for comparison only. **It is not adopted**: it is off by default and the
  decision to change the scoring policy is Tony's. Nothing here replaces or hides the recorded
  or current-gate numbers.

| Tier | Seed | Recorded | Current gate | Current min_wall (floor 1.0 mm) | Robust-v1 | Robust wall (1st pct) |
|---|---|---|---|---|---|---|
| blind | 0 | 1.000 | 1.000 | passes | 1.000 | 1.298 mm |
| blind | 1 | 0.833 | 0.833 | 0.296 mm | 1.000 | 1.023 mm |
| blind | 2 | 0.833 | 0.833 | 0.4796 mm | 1.000 | 1.393 mm |
| image | 0 | 0.833 | 0.833 | 0.3349 mm | 1.000 | 1.378 mm |
| image | 1 | 0.833 | 0.833 | 0.0105 mm | 1.000 | 1.840 mm |
| image | 2 | 0.833 | 0.833 | 0.1623 mm | 1.000 | 1.441 mm |

Means over three seeds: **blind** recorded 0.889, current 0.889, robust-v1 1.000; **image**
recorded 0.833, current 0.833, robust-v1 1.000. The current default gate reproduces every recorded
sub-score exactly (all six), which is the "old results re-score reproducibly" check.

How to read it:

- Under the default gate nothing moved: the same five designs fail `min_wall` as before, and the
  new explanations say by how much (0.01 to 0.48 mm measured against the 1.0 mm floor).
- Under `robust-v1` all six pass. That is not a finding that the designs are thick-walled: the
  `min_wall` verdict on these designs depends on the sample seed (see
  [`min-wall-analysis.md`](min-wall-analysis.md); re-sampling the same meshes flips the default
  verdict in most seeds), and `robust-v1` reads the wall over a large fixed sample, ignoring
  sliver-scale features on well under 1% of the surface. Whether sub-1% knife edges should fail a
  design is exactly the policy question left open.
- The difference between blind and image tiers (0.889 vs 0.833 under the default gate; 1.000 vs
  1.000 under `robust-v1`) stays within what three seeds cannot separate, as stated above. Under
  `robust-v1` there is no tier difference at all on this gate.
- A note on the analysis: `min-wall-analysis.md` (merged as #905) measured with the earlier gate
  that dropped every zero-area triangle, which changed two of these meshes (image seeds 1 and 2).
  It is kept as historical evidence of that gate version, and this PR appends a "Correction after
  the gate fix (#921)" section to it with the corrected readings (image seed 1 watertight, 0.0105 mm;
  image seed 2, 0.162 mm). The
  numbers in this section use the fixed gate (only isolated slivers dropped).

Method and provenance: replay of `mesh_objective_gate` with the registry spec for `sambuca`
(`min_wall_mm` 1.0, `min_bodies` 4, assembly), passing each design's own SCAD so the assembly
part-module count applies as it did in the run; Python 3.12.3, numpy 2.2.6, trimesh 4.12.2,
OpenSCAD 2021.01. The sampled minima depend on the numpy/trimesh build (#919), so exact wall values
reproduce only in that environment. The STL and SCAD stay in the gitignored `runs/` of the original
worktrees. Needs #922 (sliver fix), #903 (explanations, merged) and #918 (the robust option) to
reproduce.

Replay recipe (to reproduce after these PRs evolve): check out the `makerbench-hwe` commit that has
the robust option and the isolated-sliver helper together (the #918 branch head `5586e0b9`, which
contains #922, on top of `main` at `f9dbb74d`), in a venv from `requirements.lock`, and for each of
the six designs call `mesh_objective_gate(spec, ...)` with the registry `sambuca` spec on the
design's `output.stl`, passing the design's own SCAD as `scad_path` so the assembly part-module
count applies; call it once with defaults and once with `min_wall_estimator="robust-v1"`. Run with
`PYTHONPATH` set to that checkout so the installed package does not shadow it.
