# Ecosystem diagram

Files: `ecosystem-light.svg` / `ecosystem-dark.svg` (sources, same layout and text,
different palette) and `ecosystem-light-1200x627.png` / `ecosystem-dark-1200x627.png`
(1200 x 627 cards, rendered with `rsvg-convert -w 1200 -h 627 <svg> -o <png>`).
`ecosystem.svg` / `ecosystem-1200x627.png` are the original single-palette card, kept because the repository README embeds `ecosystem.svg`.
The alt text below is also in `alt-text.txt`. Use the light card by
default; the dark one is for dark-background surfaces. Both carry the same claims.

## Alt text

Diagram titled "MakerBench ecosystem, public pieces". On the left, two inputs
feed the Code-CAD Arena: an instrument library of 76 public instrument repos, and
CAD connectors (OpenSCAD with an optional sandboxed compile, plus SolidWorks and Fusion
job-directory backends). In the middle, one public repository, makerbench-hwe, contains the
Code-CAD Arena and the benchmark of 49 task families. On the right, the repository
publishes the MakerBench site (leaderboard, task families, arena findings) and
mirrors its leaderboard to a Hugging Face Space, which is a viewer and not the
grader.

## What is on it and where each item comes from

Source: the tracker-export piece files (`pieces/*.json`). Only pieces with
`vis: public` are used; every other piece is left out.

| On the diagram | Piece | Claim taken from it |
|---|---|---|
| makerbench-hwe | b1 | repo with benchmark and arena; 49 task families |
| Code-CAD Arena | b2 | same brief built by different setups, A/B rounds |
| CAD connectors | c4, c5 | OpenSCAD, optional sandboxed compile (`--sandboxed-compile`, off by default); SolidWorks/Fusion job-dir backends |
| Instrument library | l1 | 76 public instrument repos (from the piece's flag; the larger total in its purpose text is not shown because only the public count is on the diagram) |
| MakerBench site | s1 | built from `results/` by GitHub Pages |
| Hugging Face Space | s2 | Gradio leaderboard mirror; viewer, not grader |

## Caveats

- The piece for the Windows watcher scripts says they are labelled UNVALIDATED in
  their own headers; the diagram says "unvalidated" rather than implying they are
  proven.
- The site piece notes the data was last updated 2026-07-10 and the headline still
  names an older model; check the live site before reusing the diagram.
- The HF piece says it is unverified whether the deployed Space is the Gradio
  version. The diagram says "Gradio dashboard" as the piece does; confirm on the
  live Space.
- Numbers are as of the tracker export and may have drifted.
