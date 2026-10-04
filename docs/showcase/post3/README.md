# Vary-one-axis matchup: stub demo

A **demo of the matchup mechanism, not a result**: both entrants are `--stub`
(deterministic, zero-token), so their identical 1.000 pass rates say nothing about
any model. The real matchups are `matchup-model.md` and `matchup-backend.md` in this
folder.

## Which code

Produced from the head of PR #830 (`lane-a/matchup-mode`, commit `1206703`,
"feat(arena): preview controlled single-axis matchups") in a separate worktree;
nothing was committed to that branch. Cost: $0 (no provider CLI and no
pay-per-token entrant was invoked).

## Commands

```bash
# 1. Preview the matchup: vary the entrant model, hold everything else.
python3 -m makerbench.cli arena matchup --vary model --values stub-a,stub-b \
  --instruments ocarina --models stub-a --out preview.json

# 2. Run the two entrants with the zero-token stub and print the objective scoreline.
python3 -m makerbench.cli arena run --run-dir <scratch dir> \
  --instruments ocarina --models stub-a,stub-b --seeds 0 --stub

# 3. Studio: `python3 -m makerbench.cli arena studio --port 8791` (no --allow-live),
#    then DoE matrix -> Experiment mode "Vary one axis" -> Axis "models",
#    ocarina, entrants "stub-a, stub-b".
```

## Files

- `transcript.txt`: the output of steps 1 and 2.
- `preview.json`: the matchup preview (`varied_axis`, `held`, two cells).
- `studio-matchup.png`: Studio's DoE screen in matchup mode. It shows
  "Varying **models**. Held values: ..." and two entrants at $0 subscription.

## Caveats

- Studio's DoE screen previews and writes a queue only; it runs nothing. The
  scoreline in the transcript came from the CLI run (step 2), not from Studio.
- The screenshot notes that the ocarina reference image is not approved, so a
  queue write would skip it. That is irrelevant to the preview, but it is
  visible in the image.
- Single-voter preference scores are not involved anywhere here.
