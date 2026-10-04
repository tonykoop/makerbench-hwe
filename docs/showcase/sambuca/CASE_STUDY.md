# Case study: museum photo → parametric CAD harp

*Status: draft for Tony's review. Every claim below cites a file or URL; anything
without a source is marked **unknown**. Source repo paths are relative to
`instruments/strings/sambuca/` (the public sambuca instrument repo, read-only here);
`arena.json` = `site/data/arena.json`; registry = `tasks/code_cad_arena/registry.json`.*

## In plain English

The British Museum shows a modern reconstruction of a 4,600-year-old boat-shaped
harp from the Royal Cemetery at Ur. We gave one photo of it, plus the museum
card's dimensions, to an AI CAD assistant, and got back an editable 3D model of a
13-string boat harp after about two minutes of model thinking time (113 s). It gets the size and general shape
right but misses details (the curved neck, gold collar, lapis inlay), and an
automatic check failed a provisional wall-thickness gate (a 0.015 mm reading against a 1 mm floor; what caused that reading is unknown). So it is a
useful first draft, not a finished design, and one run is not enough to say
which AI tool is best.

## 60-second version

A 2015 museum-gallery photo of a reconstructed Sumerian boat-shaped harp went in;
a **parametric, sliders-editable CAD model** came out after about two minutes of
model "thinking" time (113 s). It is recognisably a boat-hulled arched harp with 13
strings, at the museum-card dimensions (650 × 150 mm hull, 810 mm tall). It is
**not** a faithful replica: it misses the J-curve neck, the gold collar and the
lapis band, and it **fails one of six objective gates** (minimum wall
thickness). That gap is the interesting part — a model can make something that
*looks* right and still fails a provisional buildability gate.

| | |
|---|---|
| Input | one museum photo (`images/SambucaPhotobyDonHitchcock_BritishMuseum.jpg`) + a text brief with the card dimensions |
| Tool combo | CADAM (local, GPLv3) + Claude Fable 5 → BOSL2/OpenSCAD → STL |
| Objective result | 0.833 = 5 of 6 sub-gates pass (`arena/cadam-fable-image-pilot/gate.json`) |
| Failed gate | `min_wall`: 0.0149 mm measured vs 1.0 mm floor |
| Status | generated model, **not** a measured master (`arena/cadam-fable-image-pilot/README.md`) |

## 1. The story, with sources

**Task.** The registry defines the `sambuca` task: a multi-part boat-harp
assembly, 13 strings, body 650 × 150 × 200 mm, instrument height 810 mm, keel
soundport, at least 4 distinct bodies, `min_wall_mm` floor 1.0
(`tasks/code_cad_arena/registry.json`, entry `sambuca`). The 1.0 mm floor is
described as *provisional pending first-run calibration*
(`tasks/code_cad_arena/registry.json` notes: "provisional pending first-run calibration").

**Image-conditioned run (the one that used the photo).**
- Pipeline: photo → CADAM → Claude Fable 5 via OpenRouter → BOSL2 OpenSCAD →
  in-browser STL export → `mesh_objective_gate`
  (`arena/cadam-fable-image-pilot/provenance.json`, `pipeline`).
- Date 2026-07-02; model "thinking" 113 s (`provenance.json`, `thinking_seconds`;
  also visible in `arena/cadam-fable-image-pilot/preview.jpeg`).
- Prompt and turns: the CADAM screenshot shows the brief (hull 650 × 150 × 200,
  flat soundboard, single continuous curved neck to 810 mm, 13 peg positions,
  string-holder strip, keel soundport "not the soundboard", distinct bodies,
  "use the museum photo attached to my first message as the visual reference"),
  then one generation (113 s) and a short summary turn (9 s). The screenshot's
  text is clipped at the right edge, so **the exact prompt wording and total turn
  count are unknown**; the full conversation lives only in the local CADAM
  instance (`provenance.json`, `conversation` = a `localhost` URL).
- Output: 881 bodies, all watertight; bbox 668.6 × 149.9 × 841.9 mm; largest
  body 1,648,090 mm³ (`gate.json`, `metrics`). Editable parameters include
  `string_count`, `body_*_mm`, `neck_height_mm`, wall thicknesses,
  `soundport_radius_mm`, `show_strings` (`arena/cadam-fable-image-pilot/README.md`).
- Gate sub-scores: renders 1.0, watertight 1.0, nonzero_volume 1.0,
  fits_envelope 1.0, body_count 1.0, **min_wall 0.0** → 0.8333, `passed: false`
  (`gate.json`).
- Cost of this run: **unknown** (not recorded in `provenance.json`).

**Text-only comparison (no photo).** The Round 2 export for the same task came
from `claude-code-sonnet` reading only the text brief: also 0.833, also failing
only `min_wall` (`arena/round2/provenance.json`). It has a cleaner single-arc neck
but no tuning pegs. Round 2 overall, across 4 instruments × 2 seeds
(8 trials/entrant), objective pass rates: claude-code-fable 0.8958,
claude-code-sonnet 0.875, codex-gpt-5.5 0.6875, antigravity-gemini-default
0.5833, claude-code-opus 0.5208 (`site/data/arena.json`, round 2). Only objective
scorelines are used here; preference-vote numbers are deliberately left out.

### Which combo was "best"? — an honest answer

**Not established.** On `sambuca` specifically we have exactly one image-tier
run (n = 1) and one text-only export, tied at 0.833 with the same failing gate.
The defensible claim is narrower: *CADAM + Fable 5 is the only combination in our
records that was shown the photo, and it produced a parametric, editable model
that hits 5 of 6 objective gates.* "Best combination" should not be claimed
without more seeds. `docs/CODE_CAD_ARENA_ROUND1.md` says the CADAM lane "topped
Round 3's objective board", but that is a different instrument set, so it
supports "worth trying" rather than "best on sambuca".

## 2. What is the object? (verified vs assumed)

| Claim | Status | Source |
|---|---|---|
| Boat-shaped harp from the Royal Cemetery at Ur, c. 2600 BC (Sumerian / Early Mesopotamia) | **Verified** (museum record, second-hand via search snippet; BM site returned 403 to our fetch) | [BM collection 1928,1010.1.b](https://www.britishmuseum.org/collection/object/W_1928-1010-1-b); `README.md` |
| The object in the photo is a **modern reconstruction**; the ancient survivals are the gold cap, 13 gold-headed pegs and lapis/gold inlay pieces; the wooden body was rebuilt in 1971–72 from cylinder-seal imagery and Woolley's notes | **Verified** in the search-result summary of the BM record; consistent with `README.md` and `reverse-engineering.md`. Original BM page not readable by us | same BM link (reconstruction is "121198,c") |
| The museum calls it a "boat-shaped harp", not a lyre | **Partly verified.** The quote comes from Tony's transcription of the gallery card (`reverse-engineering.md`, "Taxonomy — locked"). We have not seen the card image; the label visible in the photo is small and belongs to the neighbouring case | `README.md`, `reverse-engineering.md` |
| Museum number "BM 121198" | Repo usage is "121198 (b/c)"; BM record splits the ancient parts (1928,1010.1.b) from the reconstruction (121198,c). **Cite both, or say "reconstruction of the Ur boat-shaped harp"** | `reverse-engineering.md`; BM link above |
| "Sambuca" | **Tony's project/brand name**, not the museum's term. The repo's accepted naming pool is "sambuca / boat-harp / sailboat harp / arched harp" (`reverse-engineering.md`) | `reverse-engineering.md` |
| "Egyptian" | **Not supported.** Do not use. The only Egyptian mention in the repo is a comparison to Egyptian arched harps' string-holder pattern | `reverse-engineering.md` |
| Dimensions 650 × 150 mm, 810 mm height, 13 strings | From the BM display card as transcribed by Tony; card not re-checked | `reverse-engineering.md` "Measured Values" |

**Caption rule for the post:** "Reconstruction of the boat-shaped harp from the
Royal Cemetery at Ur (c. 2600 BC), British Museum." Call the CAD model "my
sambuca-style model" or "a boat-harp model" — never call the museum object
"sambuca", and never call the reconstruction ancient wood.

## 3. Photo license — **unclear; do not rely on it**

Three statements exist and they do not fully agree:

1. **Our repo** credits the photo as "© Trustees of the British Museum, CC BY-NC-SA
   4.0; photo Don Hitchcock, 2015" (`capstone-manifest.json`, `sources[0].credit`;
   `capstone-deck.md`; `photo-shotlist.md`: "CC BY-NC-SA 4.0 — credit required").
   This looks like the British Museum's licence for *its own* images conflated
   with the photographer's terms. Whether Hitchcock's photo carries that licence
   is **unverified**.
2. **Don Hitchcock's site.** A fetch of donsmaps.com homepage (result summarised
   by the fetch tool, not verbatim-verified; `/copyright.html` returned 404)
   reported that he lets anyone use and alter his own maps and photographs
   "at no charge, and without asking permission", asks for credit
   ("Photo: Don Hitchcock, donsmaps.com"), and excludes third-party works.
   We did **not** locate the specific page for this image.
3. **The museum object itself**: BM image licences and any gallery-photography
   rules were not readable (403). **Unknown.**

**Does a LinkedIn post showcasing the technology count as "non-commercial"?**
**Unclear.** The post is not a sale, but it promotes a project and its tooling
publicly, and there is no authoritative reading of that in our sources. If the
NC-SA terms were the operative ones, we would have to treat it as possibly
commercial, and share-alike would also apply. We are not offering a legal
conclusion. This is why the fallback below is the default, and it stays the
default **unless Tony explicitly clears the photo**.

**Verdict:** the photo *probably* may be reused with credit under Hitchcock's
stated terms, but we cannot show that this file is covered, and if the repo's
NC-SA claim is the operative one, a public promotional LinkedIn post may not count as
"non-commercial" and would need share-alike. **Recommendation
(fallback, in order):**
1. Post **only our own renders**; describe the museum object in words and link to
   the [BM record](https://www.britishmuseum.org/collection/object/W_1928-1010-1-b).
2. If Tony wants the photo in the post, first get a one-line written OK from Don
   Hitchcock (contact via donsmaps.com), then credit exactly
   "Photo: Don Hitchcock, donsmaps.com" and keep the BM-record link.
3. The photo is **not** committed to this repo.

## 4. Photo → result side-by-side plan

Layout (1200 × 627 or a 2-up carousel), left = reference, right = ours:

| Slot | Left | Right |
|---|---|---|
| 1 (hero) | museum photo *(only if license cleared; otherwise a link card to the BM record)* | `assets/round-image-tier-fable-cadam.png` |
| 2 | same photo, callouts: J-curve neck, gold collar, lapis band, 13 gold pegs | same render, callouts: straight-ish neck, no collar, no inlay, 13 pegs + strings |
| 3 | — | `assets/round2-text-only-sonnet.png` — text-only, no photo |
| 4 | Scoreboard tile: 5/6 gates; `min_wall` 0.0149 mm vs 1.0 mm floor | — |

Rules: same camera angle where possible; label the right side "generated, not a
measured master"; keep the "what it missed" callouts — they are the credibility.

## 5. Tool combo

- **CADAM** (local, GPLv3): chat-to-CAD app with a live parameter panel and STL
  export (`preview.jpeg`, `provenance.json`).
- **Claude Fable 5** as engine via OpenRouter; a local patch made the provider
  fall back to OpenRouter (`provenance.json`, `notes`).
- **BOSL2 + OpenSCAD** for the parametric source
  (`arena/cadam-fable-image-pilot/sambuca-cadam-fable.scad`).
- **MakerBench mesh gate** `makerbench.code_cad_arena_runner.mesh_objective_gate`
  for objective scoring (`gate.json`).

## 6. What worked / what failed

**Worked**
- Image + short spec → a parametric model at the right envelope in a single recorded generation (total turn count unknown)
  (bbox 668.6 × 149.9 × 841.9 mm vs 650 × 150 × 810 spec; `gate.json`).
- Editable: 13 named parameters with sliders, `show_strings` toggle (`preview.jpeg`).
- Five of six objective gates pass, including watertight on all 881 bodies.

**Failed / weak**
- `min_wall` = 0.0149 mm vs 1.0 mm floor (`gate.json`). Which feature causes it
  is **unknown** (not analysed; 1.6 mm display strings are included in the
  export per `provenance.json`, so strings are a candidate but not confirmed).
- Shape fidelity, by eye against the photo and `preview.jpeg` (our judgement,
  not a scored metric): neck is a nearly straight pole rather than the J-curve;
  no gold collar; no lapis seam band; hull reads as a shallow dish; pegs stand off
  the neck as round beads.
- The 1.0 mm floor is provisional, so the failure may partly reflect gate
  calibration rather than the model alone (`docs/CODE_CAD_ARENA_ROUND1.md`).
- n = 1 for the image-tier run; no repeat seeds.

## 7. Honest limits (put these in the post or the comments)

- Generated model; **not** a measured master, not a build packet
  (`arena/*/README.md`).
- The reference is itself a 1971–72 reconstruction, not ancient wood.
- Cost, exact prompt, and turn count for the image run are unknown.
- "Best combination" is unproven on this instrument (n = 1).
- Photo licence unresolved (section 3).

## 8. Candidate LinkedIn posts (each ≤ 1,300 characters)

**Draft A — the honest-gap hook**

> I gave an AI a museum photo of a 4,600-year-old boat-shaped harp and asked for CAD.
>
> After about two minutes of model thinking I had a parametric model: 650 × 150 mm hull, 13 strings, the key dimensions as sliders.
>
> Then I ran it through an objective mesh gate. It passed 5 of 6 checks. The one it failed: minimum wall thickness — 0.015 mm against a 1 mm floor. Looked right on screen; failed a provisional wall-thickness gate.
>
> That gap is why I build MakerBench: a benchmark that scores what AI-generated hardware designs can actually do, not just how they look.
>
> Stack: CADAM + Claude Fable 5 → OpenSCAD/BOSL2 → mesh gate.
>
> Honest limits: it's a generated model, not a measured master; the museum piece is itself a 1970s reconstruction of the Royal Cemetery of Ur harp (British Museum); and I've run this once, so I'm not claiming a best model.
>
> Next: fix the neck curve and re-run.
>
> #CAD #AI #Luthier #OpenSCAD #MakerBench

**Draft B — the maker angle**

> Museum photo in. Parametric harp out.
>
> The British Museum displays a reconstruction of a boat-shaped harp from the Royal Cemetery at Ur (c. 2600 BC). I've been designing my own modern take on it.
>
> Experiment: hand a photo plus the card dimensions to an AI CAD tool and see what comes back.
>
> Result (CADAM + Claude Fable 5, OpenSCAD): a 13-string boat harp, 650 mm hull, 810 mm tall, the key dimensions as sliders. Recognisably a boat harp. Also clearly missing the J-curved neck, gold collar and lapis band.
>
> Objective check: 5 of 6 gates pass. Wall thickness fails by a mile.
>
> What I take from it: AI is a great first-draft partner for parametric CAD. The last mile — proportion, curvature, wall thickness, manufacturability — still needs an engineer.
>
> Render below; link to the museum record in the comments.
>
> #Lutherie #CAD #AI #Engineering

**Draft C — the benchmark angle (shortest)**

> "Looks right" and "can be built" are different skills.
>
> Test case: one museum photo of a reconstructed Sumerian boat harp, one AI CAD pipeline (CADAM + Claude Fable 5 → OpenSCAD).
>
> It returned a fully parametric 13-string model after about two minutes of model thinking (113 s), and it fit the target envelope within a few percent (669 × 150 × 842 mm vs 650 × 150 × 810 mm).
>
> Our objective mesh gate passed 5 of 6 checks. It failed minimum wall thickness: 0.015 mm vs a 1 mm floor.
>
> Across MakerBench Arena rounds, human preference and the objective gate rank models almost independently (mean Spearman ρ ≈ 0.07 over rounds 6–10, three entrants per round, so directional only). That's the point of measuring both.
>
> Caveats: single run, generated model not a measured master, and I don't claim this is the best pipeline for this task.
>
> #AI #CAD #Benchmarks #MakerBench

<!-- claim: 669 source: docs/showcase/sambuca/CASE_STUDY.md#re:bbox ([\d.]+) × [\d.]+ × [\d.]+ mm -->
<!-- claim: 150 source: docs/showcase/sambuca/CASE_STUDY.md#re:bbox [\d.]+ × ([\d.]+) × [\d.]+ mm -->
<!-- claim: 842 source: docs/showcase/sambuca/CASE_STUDY.md#re:bbox [\d.]+ × [\d.]+ × ([\d.]+) mm -->
<!-- claim: 650 source: tasks/code_cad_arena/registry.json#/instruments/4/constraints/body_length_mm -->
<!-- claim: 810 source: tasks/code_cad_arena/registry.json#/instruments/4/constraints/instrument_height_mm -->

Notes on the drafts: "4,600 years" = c. 2600 BC + 2026; the repo says "~4500", so
either is fine — pick one. Draft C's ρ figure is `site/data/arena.json`
`headline.value` 0.0732 (rounds 6–10); it comes from the arena's preference-vs-gate
comparison, and the repo's public policy is to publish only that agreement
number, not preference scores. Drafts assume Tony is the designer of the sambuca project (`README.md`) and owns MakerBench — edit if not. Character
counts are checked in the PR description.

## 9. Asset checklist

Ours, in `docs/showcase/sambuca/assets/`:
- [x] `round-image-tier-fable-cadam.png` — OpenSCAD preview render of
      `sambuca-cadam-fable.scad`, cropped/2× upscaled (our render of the generated model)
- [x] `round2-text-only-sonnet.png` — Round 2 export preview
      (`arena/round2/sambuca-arena-winner.png`)

Still needed (none exist yet):
- [ ] A better hero render: shaded, matching camera angle to the photo (Blender or OpenSCAD `--render` with BOSL2)
- [ ] Photo-vs-model annotated slide (blocked on photo licence decision)
- [ ] A one-tile scoreboard graphic (5/6 gates, `min_wall`)
- [ ] Decision: neck-curve fix + second run for a real "after" (would need a new run; none made here)

Deliberately excluded:
- The Hitchcock photo (third-party; not committed).
- `preview.jpeg` — a screenshot of the CADAM UI including the chat transcript and account label; re-render instead.
- STL/SCAD sources (repo policy: no source artifacts in the public repo; STL is 32 MB).
- Vote counts / preference Elo (policy in `site/data/arena.json`).
