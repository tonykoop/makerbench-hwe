# Ecosystem diagram — LinkedIn post 1 (2026-10-06)

Files: `ecosystem.svg` (source), `ecosystem-1200x627.png` (LinkedIn size, rendered
from the SVG with `rsvg-convert -w 1200 -h 627`).

## Alt text

Diagram titled "MakerBench ecosystem, public pieces". On the left, two inputs
feed the Code-CAD Arena: an instrument library of 76 public instrument repos, and
CAD connectors (OpenSCAD in a sandbox, plus SolidWorks and Fusion job-directory
backends). In the middle, one public repository, makerbench-hwe, contains the
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
| CAD connectors | c4, c5 | OpenSCAD via sandbox; SolidWorks/Fusion job-dir backends |
| Instrument library | l1 | 76 public instrument repos (from the piece's flag; the larger total in its purpose text is not shown because only the public count is on the diagram) |
| MakerBench site | s1 | built from `results/` by GitHub Pages |
| Hugging Face Space | s2 | Gradio leaderboard mirror; viewer, not grader |

## Caveats for the poster

- The piece for the Windows watcher scripts says they are labelled UNVALIDATED in
  their own headers; the diagram says "unvalidated" rather than implying they are
  proven.
- The site piece notes the data was last updated 2026-07-10 and the headline still
  names an older model; check the live site before posting.
- The HF piece says it is unverified whether the deployed Space is the Gradio
  version. The diagram says "Gradio dashboard" as the piece does; confirm on the
  live Space.
- Numbers are as of the tracker export and may have drifted.
