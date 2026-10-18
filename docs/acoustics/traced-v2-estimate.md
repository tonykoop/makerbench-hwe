# Traced-v2 separate acoustic estimate

Revised #1113 / #1126 / #1127 lane. This is local model output, not a physical
measurement, target-note pass/fail or a published benchmark rate. See the
[model, sources, reproduction command and limitations](local-tongue-predictor.md).

The real assembly and all six linked parts were copied from Tony's named
`fixture-v2/traced-v2` source into gitignored scratch before processing. All seven
copies match their read-only originals. Neither native geometry nor sampled
width profiles are committed. The complete JSON is retained locally at
`.scratch-wolfram/acoustic-estimate.json` in the lane worktree.

- Assembly SHA-256: `7167822ee5238b29a6c17c8e87ae46eeab30747d87fd55d53d7b65e4a52442de`.
- Top-panel FCStd SHA-256: `23a1a2f1fc9a5c9d647e2f5a882c0b9be3eb0fed729f05693546d04c1f89e7f1`.
- Saved BREP SHA-256: `9fe41324df91836df2289e1dfd595725893abdc75b65ab794673b08b8be21eaf`.
- Retained report SHA-256: `77e3ea3ad93972af38f28496f4380d0f620136d125f773e6f34cd6165bc31a7a`.
- Measured thickness: 12 mm. Local-X assumed clamp planes: 80 and 377 mm.
- Saved geometry only; no native recompute, nominal-table measurement or target assignment.
- 160 midpoint-width Hermite elements per tongue, 0.02 mm footprint deflection.
- FCStd assembly and standalone BREP extraction: all ten full profiles identical.
- Maximum 80-to-160 mesh difference: 0.005404% (coarse widths averaged from fine pairs).
- Focused tests: 42 passed, including opt-in generated native BREP controls.
- Uniform-cantilever formula error at 160 elements: at most 0.012%; required <1%.
- Optional Wolfram cross-check: skipped, deterministic stub; zero new live calls.

IDs sort by fixed root-section Y, then side. Dimensions are mm. Each species
column is predicted Hz followed by the nearest equal-tempered note (A4 = 440 Hz).
The three columns are alternative material assumptions, not claims about the
species or measured stiffness of this board. Rounded values are display estimates.

| ID | Root side | Free L | Neck width | Head width | White oak Hz / note | Padauk Hz / note | Sapele Hz / note |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| T01 | right | 105.96 | 24.13 | 41.03 | 638.6 / D#5 | 712.2 / F5 | 714.7 / F5 |
| T02 | left | 102.89 | 21.20 | 40.22 | 608.8 / D#5 | 679.0 / F5 | 681.4 / F5 |
| T03 | left | 144.14 | 52.17 | 55.85 | 421.4 / G#4 | 469.9 / A#4 | 471.6 / A#4 |
| T04 | right | 154.63 | 49.70 | 93.17 | 291.6 / D4 | 325.2 / E4 | 326.4 / E4 |
| T05 | right | 94.67 | 22.62 | 24.99 | 906.5 / A#5 | 1011.0 / B5 | 1014.6 / B5 |
| T06 | left | 125.13 | 23.94 | 40.28 | 442.1 / A4 | 493.0 / B4 | 494.8 / B4 |
| T07 | right | 150.80 | 22.30 | 60.22 | 252.3 / B3 | 281.4 / C#4 | 282.4 / C#4 |
| T08 | left | 145.62 | 24.87 | 62.86 | 270.9 / C#4 | 302.2 / D4 | 303.3 / D#4 |
| T09 | right | 110.61 | 20.44 | 37.77 | 565.3 / C#5 | 630.5 / D#5 | 632.7 / D#5 |
| T10 | left | 102.82 | 23.34 | 42.02 | 669.2 / E5 | 746.4 / F#5 | 749.0 / F#5 |

Two tongues have transverse head slices containing two wood intervals. The solver
adds their widths and neglects lateral/torsional motion. Clamp position, stock
properties, grain and shell/cavity coupling remain uncalibrated. Targets and
cents-from-target remain null. These predictions must not be treated as tuned
instrument evidence. Grader/card integration and physical tolerances remain the
subsequent dependency-gated stories.
