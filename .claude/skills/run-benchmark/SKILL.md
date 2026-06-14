---
name: run-benchmark
version: 0.1.0
last-updated: 2026-06-13
description: >-
  Drive an AI-agent benchmark contribution end-to-end — run the harness on a
  model, scrutinize and aggregate the results, file issues, open pull requests,
  and submit leaderboard rows — with verify-don't-trust integrity discipline so
  nothing you do contaminates the benchmark or fakes a score. Works for any
  agentic benchmark, not just one: it first reads the target repo's own contract
  (AGENTS.md / CONTRIBUTING.md / submission docs) and detects its harness, then
  maps that onto a common five-phase workflow. MakerBench-HWE is the worked
  reference. Use this whenever the user wants to benchmark a model, evaluate an
  agent against a benchmark or leaderboard, run an eval harness, reproduce or
  regrade results, aggregate or sanity-check benchmark scores, file an issue or
  PR against a benchmark repo, or submit/verify results to a leaderboard — even
  if they just name a benchmark (MakerBench, SWE-bench, a CAD/eval repo) and say
  "run it" or "submit my results." Never trains on benchmark data, never touches
  private oracles/answer keys, never hand-edits a score or sets its own
  verification status.
---

# Run Benchmark

Agentic benchmarks are only worth running if their numbers can be trusted. The
whole point of a benchmark is to be a yardstick — and a yardstick that anyone can
quietly bend is worthless. So this skill is not just "type the run command." It
is the **discipline** that keeps a contribution honest: reproduce before you
claim, never contaminate the test set, keep public surfaces clean, and report
failures as failures. Get that discipline right and the leaderboard stays
meaningful; get it wrong and you've poisoned the well for everyone.

It also has to be **portable**. Every benchmark has its own commands, its own
public/private boundary, its own submission channel. Hardcoding one benchmark's
quirks would make the skill useless for the next one. So the first move is always
to *learn the target benchmark's own rules*, then apply a workflow that's the
same in shape everywhere.

## Phase 0 — Orient (always do this first)

Never assume you already know a benchmark's contract; assumptions are how people
leak answer keys or push artifacts that get rejected. Spend the first few minutes
discovering how *this* benchmark works:

1. **Read the repo's own agent/contributor docs**, in this order if present:
   `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `README.md`, and anything under
   `docs/` matching `*SUBMISSION*`, `*CONTRIBUT*`, `*ATTEST*`, `*INTEGRITY*`,
   `*CANARY*`, `*DATA_POLICY*`. These define the contract — follow them over this
   skill if they conflict (the benchmark's maintainers own the rules).
2. **Detect the harness** — how a run is invoked and graded. Look for a console
   entry point (`pyproject.toml` `[project.scripts]`, a `Makefile`, a `scripts/`
   runner), a results schema/example, and where result files live.
3. **Map the public/private boundary** — what must never be committed publicly
   (answer keys, oracle/gold solutions, held-out seeds, solved source artifacts)
   and where private material lives (a submodule, a separate private repo).
4. **Find the integrity markers** — a contamination canary/GUID, a do-not-train
   notice, a "verification status" concept, a maintainer regrade/attestation step.

Write down (to yourself, or to the user) the concrete answers: the run command,
the grade/reproduce command, the results path, the canary, the private boundary,
and the submission channel. That filled-in map drives the rest.

**If the target is MakerBench-HWE**, you can skip the discovery and use
[`references/makerbench.md`](references/makerbench.md) directly — it is the
worked reference with exact commands and paths. For any **other** benchmark, use
[`references/orienting.md`](references/orienting.md) to fill the same map from the
repo's own docs.

The integrity rules below are the portable spine — they hold for every benchmark.
The depth version, with the reasoning behind each, is in
[`references/integrity.md`](references/integrity.md); read it before a first
submission to a benchmark you don't know well.

## The verify-don't-trust spine (every phase)

These hold regardless of which benchmark you're working in. They exist because a
benchmark's value is fragile in specific ways:

- **Don't contaminate the test set.** Never train, fine-tune, distill, or
  retrieval-index on benchmark tasks, oracles, or results. Preserve any
  contamination canary verbatim — it's a do-not-train marker and a leak detector.
- **Never read or expose the answer key.** Gold/oracle solutions and held-out
  seeds are off-limits to a model under evaluation *and* must never be copied into
  a public surface (issue, PR, commit, log, mirror). Solving by peeking is
  cheating; leaking makes the task memorizable forever.
- **Reproduce before you claim.** A score you can't regrade locally is a rumor.
  Re-run the benchmark's own grader/reproduce step on your output and confirm it
  matches before you submit. Never hand-edit a score, level, or artifact hash to
  make it agree.
- **Report infrastructure failures honestly.** A CLI timeout, auth failure, rate
  limit, or missing local tool is an errored run (often a recorded zero), not
  model performance. Mark it as the benchmark marks errors; don't launder it into
  a score, and don't hide it.
- **Keep public surfaces clean.** If the benchmark says public PRs are
  metadata-only (no solved source artifacts), respect that to the letter — the
  sources go through the private channel. Don't route answer/source material
  around an audit.
- **Don't grade yourself.** Verification status (`verified`, `official`, etc.) is
  assigned by the benchmark's ingest/maintainer after their regrade — not by the
  submitter. Submit at the default/unverified state and let the process raise it.
- **Don't patch the harness to win.** Scores come from the unmodified public
  grader. Improving a grader is a separate, legitimate PR — never one tuned to
  flatter a specific result.

When any rule here conflicts with a human maintainer's explicit instruction, the
human wins — but flag the tension so it's a decision, not an accident.

## The five phases

Pick up at whichever phase the user needs; they're a pipeline but each is usable
alone. For exact MakerBench commands see `references/makerbench.md`; the shapes
below are benchmark-agnostic.

### 1. Run the harness
Produce result rows for a model on one or more tasks.
- Choose the agent adapter that matches how the model is called (API key vs. a
  logged-in subscription CLI). Disclose the exact `model_identifier` and any
  reasoning/effort setting — rows with different effort must not be conflated.
- Use the benchmark's **public/dev seed set** for community runs; held-out
  "official" seeds are maintainer-only and need private access.
- Each run errs toward honesty: if the runtime exposes token/cost, record it; if
  it doesn't, leave it null rather than guessing. Mark errored runs as errors.
- Long or many-model runs are easy to lose to a sleeping laptop or a hung CLI.
  Prefer one stable lane you can verify over many fragile ones; checkpoint by
  committing completed rows before starting the next batch.

### 2. Scrutinize / aggregate results
Turn raw rows into something trustworthy and legible.
- Rebuild the leaderboard/aggregate with the benchmark's own build step, then
  read it critically: implausible jumps, a model that suddenly recites the canary,
  scores that don't reproduce, or rows missing required metadata are all signals.
- **Regrade to audit.** Re-run the benchmark's reproduce/regrade on a suspicious
  row against its local artifact. If it doesn't reproduce, that's a finding —
  raise it (phase 3), don't bury it.
- Keep means honest: exclude infra-errored runs from a model's mean if the
  benchmark's convention says to, and never silently drop inconvenient rows.

### 3. File issues
When scrutiny finds a bug, gap, or contamination concern, write it up.
- Use the repo's issue templates. Make it reproducible: command, expected vs.
  actual, file paths — **never** pasted oracle data, held-out seeds, or solved
  source. Describe the problem with public metadata only.
- Open an issue *before* a large or design-touching PR, so a maintainer can weigh
  in before you build. Cheap to ask, expensive to redo.

### 4. Open PRs
- Branch off the default branch; **one concern per PR**; sign off if the repo uses
  DCO (`git commit -s`). Keep result submissions separate from code changes.
- If public PRs are metadata-only, commit only the metadata/grades and the
  regenerated aggregate — **no solved source artifacts**, even transiently (an
  artifact audit usually runs in CI and will reject them; more importantly it
  contaminates history).
- Keep CI green: the benchmark's unit tests, oracle/grader self-test, artifact
  audit, and result regrade are the gates. Don't merge around a red gate — fix the
  cause.

### 5. Submit / verify leaderboard rows
This is where verify-don't-trust is most load-bearing.
- **Self-check first:** run the benchmark's local regrade/reproduce on your bundle
  to prove it reproduces, with no answer-key access needed (good benchmarks grade
  from task parameters, not a stored solution).
- Open the metadata-only public PR; supply solved source artifacts through the
  benchmark's **private** channel exactly as its submission doc specifies
  (naming, layout, hashes). Don't invent your own layout.
- A maintainer runs the regrade/attestation; verification status flips only on
  that trusted result. Leave it at the default in your PR — you don't set it.
- If a row can't be reproduced or its source can't be supplied, it stays
  unverified. That's fine — an honest unverified row beats a fake verified one.

## A quick decision guide

- "Benchmark model X / run the eval" → Phase 0 orient, then Phase 1.
- "Are these results trustworthy / aggregate the board" → Phase 2 (regrade
  suspicious rows).
- "Something's wrong with task/grader Y" → Phase 2 to confirm, Phase 3 to file.
- "Submit my results / get them verified" → Phase 5 (self-regrade → metadata-only
  PR → private sources → let maintainer attest).
- "Make this a maintainer-verified/official row" → that's the benchmark's
  maintainer path; you prepare and reproduce, they attest. Don't self-assign it.

## Files in this skill

- [`references/makerbench.md`](references/makerbench.md) — the worked reference:
  exact MakerBench-HWE commands, paths, public/private boundary, submission +
  attestation flow. Read this when the target is MakerBench.
- [`references/orienting.md`](references/orienting.md) — how to fill the Phase-0
  map for an unfamiliar benchmark from its own docs and repo structure.
- [`references/integrity.md`](references/integrity.md) — the verify-don't-trust
  spine in depth, with the reasoning behind each rule and the failure it prevents.
- [`scripts/bundle_preflight.py`](scripts/bundle_preflight.py) — a generic,
  dependency-light sanity check for a result bundle before you open a PR (canary
  present, no answer-key/source paths leaking, required metadata populated). It
  does not recompute scores — that's the benchmark's regrade.
