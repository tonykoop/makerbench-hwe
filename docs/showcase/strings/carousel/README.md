# Strings carousel (10 slides, 1080 x 1350) + PDF

Story #910 (epic #907). Built from the anonymized strings gallery (`../gallery/`, #898) by
`scripts/build_strings_carousel.py`; slide text and per-axis numbers live in `content.json`.

**Status: GATED.** The three per-axis result slides quote pass rates that depend on the
`min_wall` check, which the S7 analysis (#900, PR #905, open) finds flips with its random
sample seed. Every result and design slide therefore carries a "PROVISIONAL" banner and the
carousel says no ranking is claimed. Do not post it as a comparison until the check is
calibrated and `content.json` is refreshed. Companion draft: p7 in `../../linkedin-posts.md`
(ON HOLD for the same reason).

## Slides

1 title; 2 method; 3-5 the 18 designs (six per slide, neutral labels, pass rate and failing
checks); 6 reference image (blind 0.889, image 0.833); 7 model (0.778 / 0.889 / 0.500);
8 CAD backend (OpenSCAD 1.000, CadQuery 0.944); 9 the yardstick moves; 10 what this does
not show.

Files: `slide-01.png` ... `slide-10.png`, `strings-carousel.pdf`, `alt-text.txt` (one line
per slide), `content.json`.

## Provenance of each number

| Slide | Value | Source |
|---|---|---|
| 6 | 0.889 / 0.833 | `../matchup-context.md` |
| 7 | 0.778 / 0.889 / 0.500; two arena retries, one Sol render failure | `../matchup-model.md` |
| 8 | 1.000 / 0.944 (and 0.889 to 0.944 depending on where CadQuery is scored) | `../matchup-backend.md` (PR #897, not yet on `main`) |
| 9 | flips in 3 to 10 of 10 re-samples | `../min-wall-analysis.md` (PR #905, not yet on `main`) |

## Things to know

- The grid slides come from the gallery, so the same overlap applies: nine of the 18 designs
  are the same setup (Sonnet 5.5, OpenSCAD, blind), and CadQuery previews use a flat yellow
  palette that hints at the backend. Image-tier renders are excluded (unresolved photo licence).
- The design slides name no model or backend. The three axis slides name the compared setups
  because the matchup reports do; they show only objective means. No Elo, votes or preference
  scores appear.
- The footer link is a placeholder.

## Regenerate

```bash
python3 scripts/build_strings_carousel.py --gallery docs/showcase/strings/gallery \
  --content docs/showcase/strings/carousel/content.json --out docs/showcase/strings/carousel
```

Needs Pillow and the Noto Sans (or DejaVu Sans) font files; falls back to Pillow's default
font otherwise. The slide PNGs are deterministic for the same inputs. Licence: Apache-2.0
(our own generated output).
