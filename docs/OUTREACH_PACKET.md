# Outreach Packet for Adjacent Benchmark Authors

Status: copy/paste draft packet for issue #74. Use this as starting language for
maintainer outreach, not as a claim source. Re-check live release status,
leaderboard counts, validation policies, and links before sending any message.

MakerBench's collaboration stance is simple: geometry benchmarks answer "is the
shape right?", physics benchmarks answer "does it hold?", and MakerBench asks
"can you build it?" The useful pitch is complementary alignment, not a
head-to-head.

## Shared Positioning

Short version:

> MakerBench-HWE is an open benchmark for hardware-design agents focused on
> deterministic, multi-process manufacturability checks: bend allowance, kerf,
> minimum wall, catalog fastener fit, maker handoff artifacts, and repeatable
> regrading. We are looking for places where that process-DFM layer can align
> with neighbouring CAD, simulation, taxonomy, and reverse-engineering
> benchmarks.

Use this boundary in every outreach note:

- Say "deterministic, multi-process manufacturability evaluation for agents,"
  not "the only manufacturability benchmark."
- Credit the other project first and describe MakerBench as a layer, component
  metric, adapter, or vocabulary-alignment partner.
- Do not quote stale leaderboard counts, top scores, validation policies, or
  release status unless re-checked immediately before sending.
- Do not imply CADGenBench is limited to build123d or tied to an unverified
  affiliation; describe it as a Hugging Face / HuggingAI4Engineering,
  tool-agnostic STEP benchmark.
- Treat promised releases and fast-moving project pages as dated observations,
  not permanent facts.

## Hephaestus-CCX Authors

Complementary boundary: Hephaestus-CCX grades structural physics and typed
requirements through a deterministic FEA pipeline; MakerBench grades shop-process
DFM rules before or beside that solver path.

Draft:

> Hello - we are working on MakerBench-HWE, an open benchmark for
> hardware-design agents that focuses on deterministic process-DFM checks
> rather than solver verification. Your Hephaestus-CCX work is one of the
> clearest signals that CAD-agent evaluation needs objective physics checks, and
> we see MakerBench as complementary rather than competitive: bend allowance,
> kerf, minimum wall, fastener thread engagement, and catalog/BOM realism can
> act as a fabrication prefilter before a design reaches FEA.
>
> Would you be open to comparing task-format boundaries? A useful alignment
> point might be a handoff where MakerBench contributes a deterministic
> manufacturability sub-grade, while Hephaestus-CCX remains the authority on
> structural solver requirements and assembled STEP validation.

Specific asks:

- Align task metadata for brief parameters, units, artifact paths, and pass/fail
  summaries.
- Define a FEA prefilter / solver handoff boundary: MakerBench checks
  fabrication constraints; Hephaestus-CCX checks structural requirements.
- Explore whether process-DFM failures should be reported as separate blocked
  gates instead of solver failures.

## BenDFM Authors

Complementary boundary: BenDFM contributes sheet-metal manufacturability
taxonomy and large supervised-learning data; MakerBench contributes
parameter-derived, agent-facing process checks.

Draft:

> Hello - MakerBench-HWE is an open hardware-agent benchmark with deterministic
> DFM checks for sheet metal, laser/vector output, 3D-print-style geometry, and
> catalog assembly. We have been using BenDFM's manufacturability framing as the
> vocabulary we want to align with for sheet-metal rules, because your taxonomy
> is much more precise than inventing new names for the same DFM concepts.
>
> We would value your feedback on whether our public rule catalog maps cleanly
> onto your taxonomy. Our boundary is different - MakerBench evaluates generated
> artifacts from agents, not a supervised dataset - but shared vocabulary would
> make both efforts easier to compare and cite.

Specific asks:

- Review MakerBench sheet-metal labels against BenDFM's taxonomy vocabulary.
- Identify missing manufacturability classes that should be named before they
  become scored task families.
- Keep claims precise: BenDFM is the taxonomy/dataset reference; MakerBench is
  an agent benchmark using deterministic DFM checks.

## CadQuery / build123d Maintainers

Complementary boundary: CadQuery and build123d are code-CAD substrates and
maintainer ecosystems; MakerBench is an evaluation harness that can make their
agent-generated artifacts easier to test reproducibly.

Draft:

> Hello - MakerBench-HWE is adding optional B-rep/STEP support for
> hardware-design agents, starting with build123d because it gives a
> headless-friendly Python path to OCCT topology and STEP export. We see
> CadQuery and build123d as the practical bridge between language-model agents
> and reproducible mechanical artifacts.
>
> We are not trying to crown one code-CAD dialect. The collaboration we would
> like is task-format alignment: a small, public contract for generated Python
> CAD, exported STEP, unit metadata, and deterministic topology/DFM summaries
> that tool maintainers can point agent developers at when they ask "how do I
> know if this output is usable?"

Specific asks:

- Align on a minimal Python-code-CAD plus STEP submission shape.
- Keep optional-local dependency handling clean so public CI can skip heavy OCCT
  wheels while local evaluators can run real topology checks.
- Compare deterministic topology summaries with process-DFM checks such as wall,
  clearance, and catalog-fit gates.

## CADFS / FeatureScript Dataset Maintainers

Complementary boundary: CADFS exposes large-scale real-world CAD histories in
FeatureScript; MakerBench can provide small, parameter-derived DFM tasks and
evaluation summaries that help connect design histories to fabrication checks.

Draft:

> Hello - MakerBench-HWE is an open hardware-agent benchmark focused on
> deterministic manufacturability checks and maker handoff quality. CADFS is
> exciting to us because FeatureScript design histories are a very different
> substrate from OpenSCAD or Python code-CAD, and they may be a better bridge to
> real-world editable CAD programs.
>
> We would be interested in discussing a lightweight alignment point: which
> FeatureScript metadata, operation vocabulary, or task parameters would make it
> possible to attach a process-DFM component score to generated or reconstructed
> CAD histories without pretending that MakerBench measures the same thing as
> CADFS.

Specific asks:

- Map operation and feature vocabulary to MakerBench task parameters where that
  mapping is non-answer-bearing and public.
- Identify whether deterministic DFM summaries can be attached to
  FeatureScript-derived outputs as a component metric.
- Avoid scale comparisons in outreach unless current CADFS and MakerBench
  counts have just been re-verified.

## GenCAD-3D Authors

Complementary boundary: GenCAD-3D is relevant to scan/mesh/point-cloud to CAD
reverse engineering; MakerBench can align visual and physical evidence tasks
with downstream manufacturability checks.

Draft:

> Hello - MakerBench-HWE has a reverse-engineering track on the roadmap and is
> trying to make it more than visual similarity: the end goal is a CAD or maker
> artifact that is geometrically plausible and also manufacturable. GenCAD-3D's
> scan/mesh-to-CAD direction is therefore a natural neighbour for us.
>
> Would you be open to comparing visual-task contracts? We are especially
> interested in how scanned physical-part evidence, clean parametric outputs,
> and deterministic manufacturability checks can coexist without mixing up
> "reconstructs the shape" with "can fabricate the result."

Specific asks:

- Align reverse-engineering task inputs and output metadata where possible.
- Keep shape-reconstruction metrics separate from MakerBench's DFM and handoff
  checks.
- Share language for visual evidence, scan provenance, and parametric artifact
  boundaries.

## CADGenBench / Hugging Face Benchmark Community

Complementary boundary: CADGenBench is the STEP generation/editing benchmark and
validation venue; MakerBench can contribute process-DFM component checks and a
build123d/STEP adapter path without treating CADGenBench rows as MakerBench
scores.

Draft:

> Hello - we are working on MakerBench-HWE, an open benchmark for
> hardware-design agents with deterministic DFM checks and maintainer
> regrade-attestation. CADGenBench is the neighbouring STEP-in/STEP-out benchmark
> we most want to interoperate with, because your task format and validation
> flow are already built around tool-agnostic CAD artifacts.
>
> We would like to align on a narrow bridge: MakerBench can produce STEP via its
> B-rep profile and report process-DFM summaries, while CADGenBench remains the
> authority for its own geometry, topology, and validation metrics. That could
> make a deterministic DFM component metric available to the Hugging Face CAD
> benchmark community without blurring the two leaderboards.

Specific asks:

- Align submission metadata for model, toolchain, commit, method notes, and
  validation evidence.
- Discuss whether a deterministic DFM sidecar could travel with STEP
  submissions as a component metric.
- Keep any cross-submission statement precise: their benchmark, their tasks,
  their metric; MakerBench reports the adapter path and DFM evidence.

## MARB / CADCLAW

Complementary boundary: MARB / CADCLAW grades macro-assembly and system-level
readiness; MakerBench grades the micro-DFM fabrication layer that can act as a
sub-grade gate for parts climbing an assembly-readiness ladder.

Draft:

> Hello - MakerBench-HWE is an open benchmark for hardware-design agents focused
> on deterministic, process-level manufacturability: bend allowance, kerf,
> minimum wall, catalog thread engagement, and maker handoff checks. MARB /
> CADCLAW's `pytest`-style mechanical-design engine and assembly-readiness
> ladder look like a strong collaboration fit because they frame readiness at a
> higher system level and are careful about not overclaiming head-to-head
> comparisons.
>
> Our suggested boundary is macro/micro: MARB remains the macro-assembly and
> system-integrity ladder, while MakerBench contributes a micro-DFM sub-grade
> gate for individual fabricated parts and catalog interfaces. We'd be glad to
> compare adapters, readiness vocabulary, and how a deterministic DFM failure
> should block or annotate a part before it advances up the ladder.

Specific asks:

- Map MakerBench DFM outputs to readiness-gate language without flattening the
  MARB ladder.
- Compare adapter contracts, especially the proposed CADCLAW-style bridge.
- Preserve the shared precision-over-hype stance: no head-to-head claims without
  a run both teams agree is valid.

## Follow-up Checklist

Before sending any message:

1. Re-check the target project's current public docs, repo, release status, and
   preferred contact path.
2. Remove stale numbers unless they are necessary and freshly verified.
3. Link MakerBench docs that support the ask: `DFM_RULES.md`, `BREP_PROFILE.md`,
   `LANDSCAPE.md`, `CADGENBENCH_SUBMISSION.md`, or `STRATEGY_MEMO.md`.
4. Ask for feedback or alignment; do not ask them to endorse MakerBench.
5. Keep private oracles, held-out seeds, source artifacts, and unverified result
   claims out of public outreach.
