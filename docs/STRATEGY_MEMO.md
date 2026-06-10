# Strategic positioning memo — MakerBench-HWE vs. the field

*2026-06-10 sweep. Companion to [LANDSCAPE.md](LANDSCAPE.md) /
[landscape.yaml](landscape.yaml); every factual claim below was verified
against primary sources on that date. This is the internal read — the
public-facing framing stays in LANDSCAPE.md.*

## Where MakerBench is genuinely unique (verified, not asserted)

1. **Deterministic process-DFM grading.** MUSE — the nearest neighbour on
   framing — scores manufacturability with a rubric VLM judge. Hephaestus-CCX
   is deterministic but grades *structural physics* (FEA), not process rules.
   Nobody else measures bend allowance, kerf, min-wall, or
   fastener-thread-vs-wall engagement deterministically for agents.
2. **Multi-process coverage.** Every verified neighbour grades zero
   fabrication processes; BenDFM covers exactly one (sheet-metal bending) and
   is a supervised-learning dataset, not an agent benchmark.
3. **Anti-memorization.** Verified absence across the entire field: not one
   neighbour claims contamination controls or parametric task generation.
   Hephaestus-CCX's "scrub pass" is the closest thing, and it's informal.
4. **Integrity tooling.** Only CADGenBench has anything comparable (a manual
   `unvalidated`→`validated` review tier). Canary + oracle split +
   regrade-attestation remains a field-unique stack.
5. **The blind-vs-perception gap as a headline metric.** No neighbour runs a
   two-track design; agentic neighbours (CADSmith, Physics-in-the-Loop,
   Hephaestus) build the loop into the *method*, not the *measurement*.

**Strongest claim (per the GPT-5.5 brief's question):** not "CAD generation"
(crowded), not "private verified grading" (necessary but not sufficient) —
it's **"deterministic, multi-process manufacturability evaluation for
agents."** Avoid leading with: dataset scale, B-rep/STEP depth, multimodal
input, or physics/FEA depth — others verifiably do each better today.

## Where MakerBench is behind / exposed (honest list)

- **No image/drawing input.** CADBench, BenchCAD, 3DCodeBench, UniCAD, and
  CADGenBench all take images or drawings. The blind track is text-only.
- **Scale optics.** 18k (CADBench) / 17.9k (BenchCAD) / 20k (BenDFM) vs. ~12
  task families. Parametric infinity is the better argument but loses the
  table-stakes comparison at a glance.
- **STEP/B-rep maturity.** CADGenBench gates on watertight B-rep and grades
  Betti-number topology; Hephaestus demands assembled multi-part STEP. Our
  brep-build123d profile is proof-of-life.
- **Physics depth.** Hephaestus runs gmsh+CalculiX against typed requirements.
  Our Level-3 is volumetric/mass-style. "Physics" in our four-level ladder
  over-promises relative to what an FEA-literate reader expects.
- **Single code-CAD substrate** (OpenSCAD) while the field converges on
  CadQuery/build123d (BenchCAD, CADSmith, Text-to-CadQuery, CADGenBench
  baseline) and STEP exchange.
- **Assemblies.** MUSE grades B-rep assemblies; ArtiCAD does articulated
  assemblies with URDF. Our assembly story is catalog-fastening, not
  multi-body mates.

## White space MakerBench owns outright

Bend-allowance / kerf / thread-engagement DFM for agents; catalog/BOM realism
(real part selection with geometric consequences); maker-handoff dossier
(BOM + process plan + assembly sequence); reverse-engineering as a graded
agent task (note: GenCAD-3D's 51 laser-scanned parts would make an excellent
shared fixture set); cost/repeatability accounting per run.

## Top threats, ranked

1. **Hephaestus-CCX trajectory** (2605.17448, SNU). Deterministic,
   solver-graded, assembled-STEP-from-brief, with brutal frontier-agent
   headroom (zero strict passes). It shares our grading philosophy and beats
   our physics level. If its authors add process-DFM rules, they converge on
   MakerBench from above. *Window: it's "work in progress" with no public
   release URL yet.*
2. **CADGenBench adding manufacturability components.** HF distribution, live
   leaderboard (14 entries), tool-agnostic STEP, validation tiers. Their "CAD
   Score is a weighted combination of component scores" design means a DFM
   component is one PR away. Distribution beats differentiation if this
   happens.
3. **MUSE going deterministic.** Its stage 1–2 checks are already
   deterministic-style; only design-intent/manufacturability sits with the VLM
   judge. A v2 that swaps rule-based DFM in would directly erase claim #1.
4. **Field standardizing on STEP-in/STEP-out** (CADGenBench's contract,
   Hephaestus's output), leaving an OpenSCAD-substrate benchmark looking
   niche regardless of grading quality.

## Collaboration plays

- **CADGenBench / Hugging Face** — the build123d bridge is real: their
  reference baseline is the same stack as our brep profile, and they're
  tool-agnostic STEP. Concrete asks: cross-listing, a shared STEP task-format
  note, and submitting a MakerBench-style DFM component score as a candidate
  CADGenBench metric. Contact path exists (maintainer email in their
  validation doc). **Correction from this sweep: drop all "Mecado" references
  — no primary source supports the affiliation.**
- **Hephaestus-CCX authors (SNU/OneLineAI)** — complementary deterministic
  layers (their FEA, our DFM); a joint "deterministic hardware-agent eval"
  framing helps both against judge-based benchmarks.
- **BenDFM (Ghent)** — adopt their manufacturability-metric taxonomy
  vocabulary for our sheet-metal graders; cite them, invite cross-reference.
- **GenCAD-3D (MIT)** — their 51 scanned physical parts ↔ our
  reverse-engineering family.

## Ranked roadmap moves (protect differentiation first)

1. **Ship one runnable brep-build123d task family with private gold STEP**
   (extends the landed proof-of-life; answers the STEP-maturity exposure and
   makes the CADGenBench bridge concrete instead of aspirational).
2. **Publish the DFM rule catalog as a citable artifact** (the bend-allowance
   / kerf / thread-engagement rules with formulas + tolerances, BenDFM
   taxonomy vocabulary). This stakes the white-space claim *before* a
   well-funded entrant writes it — cheap, defensible, citable.
3. **Image/drawing input via the reverse-engineering family** (assets
   plumbing already exists in `makerbench/assets.py`; closes the loudest
   modality gap on our own differentiated turf rather than chasing CADBench).
4. **Rename/harden Level 3 toward real physics, or re-label it** — and put the
   `simulation-fea` expert pack on a date. Hephaestus made "physics" mean
   CalculiX; we shouldn't invite the comparison un-armed.
5. **Broader model coverage on the existing board** (the cheapest credibility
   signal; CADGenBench's 14 rows set the bar).

## Distribution: how these benchmarks actually get traction

Verified pattern: arXiv paper + open dataset + live leaderboard + project
page (3DCodeBench: site + human-preference arena; CADGenBench: HF Space +
submissions dataset; EngDesign: NeurIPS D&B track). MakerBench has the
GitHub Pages board but no arXiv anchor and no HF presence.

**Lowest-effort visibility wins, in order:** (1) submit a MakerBench-run
model's STEP outputs to CADGenBench — instant presence on the field's only
live leaderboard, and we already run frontier models; (2) mirror the
leaderboard as an HF Space (static embed is fine); (3) a short arXiv tech
report (the four-level ladder + parametric anti-memorization + the
blind-vs-perception gap is a paper-shaped contribution already).

## Risk register: claims to avoid in public copy

- Don't say "only benchmark with manufacturability evaluation" — MUSE claims
  manufacturability (judge-graded) and BenDFM measures it (non-agent). Say
  "only *deterministic, multi-process* manufacturability evaluation *for
  agents*."
- Don't say CADGenBench is "HF × Mecado" (unverified) or restricts substrates
  to "build123d/Onshape/Autodesk" (false — tool-agnostic, build123d is a
  baseline).
- Don't cite CADSmith as a VLM-judged *evaluation* — the judge is in its
  repair loop; its eval metrics are deterministic.
- Don't call "Toward Engineering AGI" a position paper — it's the EngDesign
  benchmark (NeurIPS 2025 D&B).
- Several neighbours' releases are promised-not-shipped (UniCAD,
  Physics-in-the-Loop, Hephaestus, GenCAD-3D) — fine to note, but date-stamp
  it; this changes monthly.
- All adjudications in this sweep are abstract/README-level; re-verify before
  quoting numbers in anything outward-facing.
