# Strings gallery: anonymized, objective only

Story #884 (epic #879). Built with the merged gallery generator
(`scripts/generate_render_gallery.py`, #866) from the run directories of the three strings
matchups: context (`../matchup-context.md`), model (`../matchup-model.md`) and backend
(`../matchup-backend.md`).

Files: `index.html` (the page), `grid.png` (1660 x 1892 px, one image of all 18 designs,
for a carousel or a post), `gallery.json`, `img/` (metadata-free copies of each render) and
`alt-text.txt` (paste-ready alt text for the grid).

## What is in it

18 designs, all Claude/Codex output on the `sambuca` brief from the **written brief only**
(blind tier): the 3 blind-tier runs from the context matchup, the 9 runs of the model
matchup (3 entrants x 3 seeds), and the 6 runs of the backend matchup (2 backends x 3
seeds). Each design is labelled "Design N" in a seeded shuffled order
(`--seed strings-gallery-1`) and shows only its objective pass rate and failing checks. No
model, backend or votes appear in the page, JSON, file names or PNG metadata (re-encoded
from pixels). The label-to-entrant key was written outside the repository and is not
committed.

## What to know before using it

- **The three sets overlap.** The same setup (Sonnet 5.5, OpenSCAD, blind, sambuca) was
  run in all three matchups, so nine of the 18 designs come from that one setup. Do not
  read the grid as 18 independent setups or count pass rates across it.
- **Palette hints at the backend.** CadQuery previews are rendered in a flat yellow and the
  rest in a shaded brown; the names are hidden but the colour still separates them. The
  generator documents this limit.
- **Excluded on purpose:** the three image-tier runs from the context matchup. Their input
  was a third-party museum photo whose licence is unresolved, so reuse of those renders
  is gated (see `../matchup-context.md`).
- **Design 16 is a genuine failure** (a generated script that would not render), shown as
  "Failed before scoring (counted as 0)". Runs that errored and were retried by the arena
  appear only through their retry result.
- Pass rates are the arena's mean of six mesh checks with a provisional wall-thickness
  floor. They are not a quality ranking, and no preference data is involved.

## Regenerate

```bash
python3 scripts/generate_render_gallery.py \
  runs/code_cad_arena/s6-881-blind-seed{0,1,2} \
  runs/code_cad_arena/s6-882-{claude-code-opus-5.5,claude-code-sonnet-5.5,codex-gpt-6.1-sol} \
  runs/code_cad_arena/s6-883-{openscad,cadquery}-seed{0,1,2} \
  --out docs/showcase/strings/gallery --seed strings-gallery-1 --key-out /private/path/key.json
```

Requires the run directories from those three reports (gitignored `runs/`). Licence of
the renders and page: Apache-2.0 (our own generated output, no third-party input).
