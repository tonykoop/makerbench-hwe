# LinkedIn post drafts (p1–p7), corrected

Technology-showcase posts. Each draft is ≤ 1,300 characters. Source for the
fixes: the Lane D2 claims audit; sambuca facts come from
[`sambuca/CASE_STUDY.md`](sambuca/CASE_STUDY.md).

Status: **p1, p2, p4** are postable once the open questions under them are
answered. **p3** is gated (needs #828 and #830 merged plus one real run).
**p5**'s headline is **ON HOLD** until the frontier re-run (#668) lands; the
rest of p5 is fixed. **p6** (the benchmark caught a bug in its own scoring) is postable once its link placeholder and the check below are resolved. **p7** (string-instrument matchups) is **ON HOLD** until the min_wall analysis (#900 / PR #905) and the calibration decision it asks for land.

Rules that apply to every post: do not link or imply that the live Fusion and
SolidWorks connectors are open (they are not public); publish no single-voter
Elo, only objective scorelines and the rank-agreement number.

---

## p1: What I have been building and why

```text
I have been building something in my spare time to answer one question: how well can AI actually design physical things?

Three pieces:

- Connectors that let an LLM work in real CAD: a live Fusion connector, an early SolidWorks one, and code-CAD backends (OpenSCAD, CadQuery, build123d, Blender).
- An arena that pits setups against each other on the same brief, so I can ask how much the model, the tool and the workflow each change the outcome.
- MakerBench, which scores what comes out with objective checks: does it render, is it watertight, does it fit the size envelope, are the walls thick enough to build.

My testbed is musical instruments. A harp or a ukulele body has real constraints. It holds together and sounds right, or it does not.

Over the next month I will share what I found, including where my own blind preference and the objective scores disagree.

If you work on CAD tooling, hardware design or AI evaluation, tell me what you would want tested.

#CAD #AI #HardwareDesign #MusicalInstruments #AIEvaluation
```

Claims → source
- SolidWorks is "early", Fusion is "live": Fusion stage→confirm connector, and a 28-body kora built live through it; SolidWorks capability matrix lists most live features as broken, gap or blocked. Code-CAD backends: `makerbench/code_cad_arena_runner.py`, `docs/CODE_CAD_BACKEND_AXIS.md`. (#SolidWorks tag dropped until a SolidWorks live build is proven.)
- Same brief for each comparison: `docs/CODE_CAD_ARENA.md` (same instrument spec and seed).
- The post now *asks* whether tool and workflow matter; only the model effect is shown (Round 2 pass rates 0.52 to 0.90, `site/data/arena.json`). The one image+CADAM vs text comparison on sambuca tied at n=1.
- Objective checks: gate in `code_cad_arena_runner.py` (renders, watertight, non-zero volume, fits envelope, min wall, body count). "Envelope", not "dimensions".
- "My own blind preference": the preference scoreline is a single human's blind vote, not an AI's taste (`arena.json`, `docs/CODE_CAD_ARENA.md`).
- Harp and ukulele briefs: `tasks/code_cad_arena/registry.json`.
- Open: "in my spare time" is a personal fact the repos cannot confirm.

---

## p2: One museum photo, one CAD replica

```text
I gave an AI one photo of a museum harp and asked it to build the CAD.

The photo: the British Museum's reconstruction of a boat-shaped harp from the Royal Cemetery at Ur, c. 2600 BC. A tall neck rises from a boat-shaped hull, with 13 strings.

The setup: CADAM with Claude Fable 5, writing OpenSCAD (BOSL2), shown the photo plus the display-card dimensions. About two minutes of model thinking. The result is a parametric model I can edit, with string count, neck height and hull dimensions as sliders.

What worked: it landed on the right envelope (669 × 150 × 842 mm against a 650 × 150 × 810 mm target) and passed 5 of 6 objective checks, with every body watertight.

What did not: minimum wall thickness came out at 0.015 mm against a 1 mm floor (a floor I am still calibrating). By eye it also missed the J-curved neck, the gold collar and the lapis band.

I like this test because a replica cannot hide behind a nice render. It either renders, closes, fits the envelope and has walls you could build, or it fails a check.

Limits: one run, a generated model rather than a measured master, and the museum piece is itself a 1970s reconstruction.

Museum record in the comments. Render is mine.

#CAD #AI #GenerativeDesign #MusicalInstruments #OpenSCAD
```

Claims → source
- Object and date: BM record 1928,1010.1.b; the displayed object is a 1971–72 reconstruction (`CASE_STUDY.md` §3).
- Model, tools, image-conditioned: `sambuca/arena/cadam-fable-image-pilot/provenance.json`, `CASE_STUDY.md`. "About two minutes" = `thinking_seconds` 113.
- Parameters: `string_count`, `body_*_mm`, `neck_height_mm`, wall thicknesses, `soundport_radius_mm`. There is **no hull-curve parameter**, so the post says "hull dimensions".
- Envelope and gates: `gate.json`: bbox 668.6 × 149.9 × 841.9 mm, 5 of 6 pass, all 881 bodies watertight.
- min_wall 0.0149 mm vs a 1.0 mm **provisional** floor: `gate.json`, `docs/CODE_CAD_ARENA_ROUND1.md`. Missing neck, collar and band: by eye, `CASE_STUDY.md` §6.
- Removed: the string-clearance and neck-load claim. The gate checks render, watertight, volume, envelope, min wall and body count only.
- Tag: #Fusion360 replaced with #OpenSCAD (this pipeline did not use Fusion).

Open questions (do not post until answered)
- **Turn count: unknown.** The screenshot is clipped and the conversation lives only in a local CADAM instance. The post gives no turn count.
- **Photo licence: unresolved.** The repo credit (CC BY-NC-SA) conflicts with the photographer's site terms, and the specific image page was not found. The draft therefore uses **our render only** (`docs/showcase/sambuca/assets/round-image-tier-fable-cadam.png`). Do not attach the photo without the photographer's written OK. See `CASE_STUDY.md` §3.

---

## p3: Change one thing at a time (GATED)

Publish only after #828 and #830 are merged **and** one real matchup run exists.
Assets for this post: [`post3/`](post3/).

```text
Most AI comparisons quietly change three things at once: the model, the tool and the prompt. Then they credit the model.

I added a matchup mode to my CAD arena. You pick one axis to vary and hold everything else fixed:

- Two models, same CAD backend, same brief
- Two CAD backends, same model, same brief

First result: [FILL ONLY FROM A REAL RUN: name the varied axis, the held values and the objective pass rates]

Each result is saved with the axis that varied and the values held fixed, so a score always says what it is comparing.

The arena and the scoring code are public: github.com/tonykoop/makerbench-hwe. The live CAD connectors are not public yet.

#CAD #AIEvaluation #OpenSource
```

Claims → source
- Matchup mode and saved `varied_axis` / `held`: not merged yet (#830 draft, #828 draft). Re-verify after merge.
- "Arena and scoring code are public": makerbench-hwe is public; the live Fusion and SolidWorks connectors are in a private repo, hence the last sentence.
- "SolidWorks vs Fusion" matchup removed: SolidWorks live is largely unproven.
- "Most comparisons change three things at once": opinion.

---

## p4: An assembly built live in Fusion

```text
An LLM agent built a 28-body kora live in Fusion, one confirmed step at a time.

The bridge: a Fusion add-in that lets an agent stage geometry and parameters. Nothing touches the model until the stage is confirmed.

The agent decided what to build next, where it went and when to fix its own mistakes. The bridge enforced the discipline: stage, review, confirm, in millimetres, one coherent change at a time.

What broke: on that first build (4 July) the bridge had no delete and no bounding-box read-back, so cleanup was manual and the agent had to infer placement from exports. Both gaps came out of that session. The bridge now has delete, move and rotate, and it reports every body with its bounding box, so the agent can check its own work.

#Fusion360 #CAD #MCP #AI
```

Claims → source
- **28 bodies, a kora**, built live 2026-07-04 through the Fusion connector: the only record is the local epic notes. The earlier "31" appears nowhere. Confirm against a screenshot or the Fusion document before posting.
- Stage → review → confirm, mm units, same vocabulary for Fusion and SolidWorks: the bridge build-loop and conventions docs (private repo).
- What broke: no delete, no bbox read-back on 07-04; those now exist (same docs).
- Do not link the bridge repo or imply it is open.

---

## p5: New models on the board (headline ON HOLD)

```text
I re-ran my CAD benchmark on the newest models. [ON HOLD: headline result, only from the results files once the frontier re-run (#668) lands]

The result I keep coming back to: what I prefer and what the objective checks reward often differ. Across arena rounds 6 to 10, the rank correlation between my blind votes and the objective score averaged about 0.07, close to zero. Each round had only a handful of entrants, so treat it as a direction, not a law.

Looking right and being buildable are different skills. That is a reason to keep both scorelines and never blend them.

The full board, the method and the code: [LINK once the site refresh (#666) ships]

#AIEvaluation #CAD #Benchmark #HardwareDesign
```

Claims → source
- **Mean ρ ≈ 0.07 over rounds 6–10** (0.0732): `site/data/arena.json` headline. "Four rounds" is removed (stale: the site publishes ten, and the old −0.2 to +0.5 range holds only for rounds 1–4).
- Small n per round: `arena.json` round entries (for example R8 has n=3).
- Both scorelines, never blended: `docs/CODE_CAD_ARENA.md`, `docs/CODE_CAD_AGREEMENT.md`.
- No Elo or vote counts are published (`arena.json` policy).
- Headline and link stay ON HOLD: #668 and #666 are open.

---

## p6: The benchmark caught a bug in its own scoring

```text
My CAD benchmark just caught a bug in its own scoring.

I compared three CAD backends on one task: the same AI model, three seeds each. OpenSCAD scored 1.000. CadQuery scored 0.778 and failed the "watertight" check on all three seeds. That looked like a finding about CadQuery.

It wasn't. I re-examined the meshes: each one carried four zero-area sliver triangles from the STL writer, and the scorer counted every sliver as its own broken body. Two of the three failures were the gate's fault. The third was real: that design has a non-manifold edge.

The fix drops zero-area triangles before checking, with regression tests. Re-scoring the same meshes moved CadQuery from 0.778 to 0.889. A fresh re-run, after that fix and a prompt hint for build123d, scored 1.000 for both CadQuery and build123d.

Caveats: three seeds, one easy ocarina task, fresh generations from a non-deterministic model. It says nothing about which CAD system is better. It does say a benchmark needs a way to check its own scorer.

Write-up and code: [LINK once the site refresh (#666) ships]

#AIEvaluation #CAD #Benchmark #OpenSCAD
```

Character count: 1109 (limit 1,300).

Claims → source (all on `main` under `docs/showcase/post3/` unless noted)
- **First result: OpenSCAD 1.000, CadQuery 0.778, three seeds, `watertight` failed 3 of 3**: `matchup-backend.md`, "Before (published)" column (ocarina, `claude-code-sonnet-5.5`, blind, L1). The same table gives build123d 0.556.
- **Each mesh carried four zero-area sliver triangles from the STL writer; the scorer split each into a body that can never be watertight**: `cadquery-watertight-investigation.md`, Evidence 2 (#874, PR #886).
- **Two of three failures were the gate's; the third is a real non-manifold edge**: the same file, "Answer" table and Evidence 4.
- **Fix drops zero-area triangles (height below 1e-6 mm) before checking, with regression tests**: PR #886, `makerbench/code_cad_arena_runner.py`.
- **Re-scoring the same meshes: CadQuery 0.778 to 0.889**: `cadquery-watertight-investigation.md` and the "Old meshes, fixed gate" column of `matchup-backend.md`.
- **Fresh re-run: CadQuery 1.000, build123d 1.000 after the gate fix plus a build123d prompt hint**: `matchup-backend.md`, "After (fresh re-run)" column (PR #893; the hint is #875 / PR #888).
- **Caveats** (three seeds, one easy task, fresh non-deterministic generations, cannot prove the hint prevented the earlier crash): the PR #893 description and `matchup-backend.md` update section.
- No Elo, votes or preference scores are used anywhere in the sources.

Open questions (do not post until answered)
- **Link:** the placeholder waits on the site refresh (#666), as for p5. Alternatively link the public repo write-up directly.
- **Later strings replay:** in the strings backend matchup (PR #897) a replay of CadQuery meshes in another runtime scored one seed differently from the live run, with the cause unknown and *not* the sliver fix. That does not change this post's claims (all from the ocarina matchup), but do not extend p6 to strings without reading that report.
- **`min_wall` caveat on the quoted scores.** The S7 analysis (#900, PR #905, open) finds the `min_wall` check flips with its random sample seed. The seed-2 CadQuery mesh also fails `min_wall` (with `watertight`), so the 0.889 (and the 1.000 re-run values, which pass `min_wall`) each include that check; the `watertight` findings themselves do not. Re-check the figures if the check is recalibrated before posting.
- "I re-examined the meshes" describes the S5 investigation (#874) run by the sprint's agents; adjust the voice if the poster did not do it personally.

---

## p7: String-instrument matchups (ON HOLD)

```text
I ran three controlled matchups on a string instrument: a boat-shaped harp modelled from a written brief. Each matchup changed exactly one thing and held the rest fixed: the model, the CAD backend, or whether the AI saw a reference image.

What I can say so far: 18 trials, and 17 of them produced a recognisable arched harp; the other one was a script that would not render.

What I will not say yet: which setup is better. [ON HOLD: pass-rate numbers wait for the wall-thickness check to be calibrated.]

The most useful result so far is about the yardstick. That check estimates wall thickness from a random sample of points, and re-sampling the same mesh can change its verdict for some designs. So any score that contains it may move, and part of the run-to-run spread I saw on identical setups may be the check, not the model.

Fix the check, then compare. Until then, no ranking.

Every design is in an anonymized gallery: neutral labels, objective checks only, no model names, no votes.

Gallery and method: [LINK once published]

#AIEvaluation #CAD #Benchmark #OpenSCAD
```

Character count: 1078 (limit 1,300).

**Status: ON HOLD.** Posting waits for the S7 gate calibration: the `min_wall` analysis in `docs/showcase/strings/min-wall-analysis.md` (#900, PR #905, still open) reports that the check is close to a coin flip on its sample seed (re-sampling the same mesh fails the floor in 3 to 10 of 10 samples across meshes, and some meshes change verdict between sample seeds), and a fix needs a metric decision. Until that lands, no pass-rate number in this post is safe.

Numbers, and whether they survive calibration
- **Safe (do not depend on `min_wall`):**
  - three one-axis matchups (context, model, backend) on the `sambuca` brief: `docs/showcase/strings/matchup-context.md`, `matchup-model.md`, `matchup-backend.md`;
  - 18 trials in the anonymized gallery, of which **17 rendered designs and one script failure with no render** (Design 16, a Codex seed-1 recursion bug, counted as 0): `docs/showcase/strings/gallery/` (the three matchups overlap on one setup; see its README);
  - the 17 rendered designs are recognisable as arched harps: by eye in the gallery and the matchup renders (one reader's view, not a measurement); the failed script never reached the mesh checks and cannot be judged visually;
  - anonymized and objective-only gallery: its README and `alt-text.txt`.
- **FLAGGED, do not quote until calibrated:** every pass rate in the three reports (context blind 0.889 vs image 0.833; model 0.778 / 0.889 / 0.500; backend 1.000 vs 0.944), the "no measurable benefit from the image" line, and the identical-setup spread (0.889, 0.889, 1.000). Most of their failing checks are `min_wall`. The draft therefore quotes none of them and says the ranking is on hold.
- **Unaffected by the wall-thickness sampling, but not for this post:** the Codex seed-1 zero (a script bug) and the CadQuery seed-0 `watertight` failure; both are single trials.
- **Inference to recheck:** the draft says part of the run-to-run spread may be the check, not the model. That is a hypothesis from the #905 finding, not a measured split. My own strings reports attribute the spread to model non-determinism; that attribution is now partly unsupported.
- **Excluded assets:** the three image-tier renders (third-party photo, licence unresolved).
- No Elo, votes or preference scores anywhere.

Before un-holding: re-run or re-score the affected numbers after calibration, then replace the "on hold" sentence with the calibrated result and re-check every figure against the reports.
