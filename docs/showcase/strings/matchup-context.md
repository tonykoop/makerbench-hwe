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
