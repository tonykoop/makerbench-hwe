# Oracle Contamination Response Playbook

A benchmark's value is only as good as the secrecy of its answers. If a private
oracle, a held-out seed or fixture, an official expected answer, a cached GitHub
ref, or benchmark content inside a model's training data leaks — or is *suspected*
to have leaked — MakerBench needs a calm, repeatable response rather than a panicked
edit. This document is that playbook.

One principle governs everything below:

> **Preserve historical truth and relabel affected data. Never silently edit old
> tasks, reinterpret old scores, or delete old results to make the board look
> clean.** Contamination is handled by *labeling and freezing*, not by rewriting
> the past.

This is a process document. It changes no schema, score, site code, result row, or
oracle, and it does **not** perform the git-history scrub tracked in
[#17](https://github.com/tonykoop/makerbench-hwe/issues/17) — it only says when and how
that work is triggered. It exposes no private oracle content, held-out seeds,
hidden thresholds, or private paths.

## What "contamination" covers

Contamination is any event that lets a model, a contributor, or the public see —
or plausibly have seen — answer-bearing material it should not have. The surfaces
that can leak:

- **Private oracle repository** — the `makerbench-oracles` submodule mounted at
  `private/oracles/` (gold `oracle.scad`, solved geometry, held-out fixtures,
  official seed files; see [`TASK_PACKS.md`](TASK_PACKS.md)).
- **Held-out seeds / official answers** — the official seed set and any official
  thresholds that public dev seeds deliberately do not reveal (see
  [`SEED_POLICY.md`](SEED_POLICY.md), [`COMMUNITY_SUBMISSION.md`](COMMUNITY_SUBMISSION.md)).
- **Git history** — answer-bearing blobs still reachable in the public repo's
  history even after deletion from the working tree
  ([#17](https://github.com/tonykoop/makerbench-hwe/issues/17)).
- **GitHub cached refs / unreachable objects** — blobs that survive in pull-request
  refs (`refs/pull/*`), forks, or the API's blob endpoints even after a branch is
  deleted or history is rewritten.
- **Result-bundle artifacts and dossiers** — a submitted `results/<model>/` bundle
  whose source artifacts, traces, or dossiers accidentally embed oracle geometry or
  private fixture data.
- **Public comments / PRs / docs** — an issue comment, review, or doc that pastes a
  private path, a held-out seed value, an official threshold, or a real sensitive
  SHA.
- **Model-training exposure** — a model that reproduces the contamination canary
  GUID or a specific oracle on demand, indicating benchmark data entered its
  training set (see [`../CANARY.md`](../CANARY.md)).

## Severities

| Severity | Definition | How it's reached |
| --- | --- | --- |
| **`suspected`** | A plausible but unconfirmed exposure. Treated seriously; not yet proven. | A suspicious score jump, a partial canary recall, a third-party report, or a near-miss in a public comment. |
| **`confirmed_partial`** | Exposure is confirmed but scoped — specific task families, specific seeds, or a single bundle. | A reproduced oracle for one family, a leaked seed value, or one bundle carrying private data. |
| **`confirmed_full`** | Exposure is confirmed and broad — a whole profile, the official seed set, or the private repo / history. | A recovered oracle set, a published held-out seed list, or confirmed history/ref exposure of `private/oracles`. |

Severity can only escalate during an incident, never silently downgrade: if new
evidence narrows the blast radius, that is recorded as a *new finding with its own
note*, not an edit of the original assessment.

## Blast-radius triage

Before acting, decide the scope. The response scales with it.

- **Family-level** — one task family's oracle/fixtures are exposed; other families
  in the profile are clean. *Freeze the family, keep the rest of the profile live
  but note the carve-out.*
- **Profile-level** — enough of a profile is exposed that its scores are no longer a
  fair yardstick. *Freeze and relabel the whole `benchmark_profile` version.*
- **Repo-level** — the private oracle repo, the official seed set, or git history is
  exposed. *Treat every profile that depends on the affected oracles as
  compromised, and trigger the history/cleanup tracks.*

Decision questions: *Which oracles/seeds are provably reachable? Which profiles and
versions consumed them? Is the exposure bounded to the working tree, or does it
reach history / cached refs / forks? Could a current leaderboard row have benefited?*
When unsure, size up, not down — a family-level guess on a repo-level leak is the
expensive mistake.

## Response actions by severity

Actions are cumulative — a higher severity does everything the lower ones do, plus
more. None of them rewrite or delete historical scores.

### `suspected`

1. **Open a private incident note** (maintainer-side) recording the trigger,
   suspected surface, and a first blast-radius guess. Do **not** paste private
   paths, seed values, thresholds, or real sensitive SHAs into any public channel.
2. **Audit, read-only.** Run the relevant checklists below to confirm or rule out
   exposure. Check the contamination canary signal and recent score anomalies.
3. **Hold promotions.** Pause any pending profile/version promotion or Frontier
   refresh ([#116](https://github.com/tonykoop/makerbench-hwe/issues/116)) that would
   bake in the suspected material until the audit clears.

### `confirmed_partial`

4. **Freeze** the affected task family or profile version — no new official runs or
   leaderboard promotions against it.
5. **Relabel** the affected profile version in the
   [profile lifecycle](PROFILE_LIFECYCLE.md): mark it `contaminated` (and, once
   superseded, `retired`). Archived snapshots ([#13](https://github.com/tonykoop/makerbench-hwe/issues/13))
   keep the old board readable with the new label.
6. **Preserve old results as historical only.** Existing rows stay, marked with
   their original `benchmark_version` and the new lifecycle status; they are never
   ranked as current capability evidence.
7. **Audit result bundles and community submissions** for the affected scope (see
   the bundle checklist). Flag rows that could have benefited; never quietly edit
   their scores.
8. **Promote a replacement.** Bring in reserve fixtures or a replacement
   profile/version through the reserve-fixture promotion workflow
   ([`RESERVE_FIXTURES.md`](RESERVE_FIXTURES.md),
   [#115](https://github.com/tonykoop/makerbench-hwe/issues/115)) and the release
   checklist ([`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md),
   [#120](https://github.com/tonykoop/makerbench-hwe/issues/120)). Reserve
   fixtures stay private until explicitly promoted.
9. **Publish a short public methodology note** stating *what was affected, what was
   relabeled, and what replaced it* — without private paths, seed values,
   thresholds, or sensitive SHAs.

### `confirmed_full`

10. **Treat all dependent profiles as compromised** and relabel each per step 5.
11. **Coordinate repository cleanup.** If git history or cached refs are implicated,
    trigger the history-scrub track ([#17](https://github.com/tonykoop/makerbench-hwe/issues/17))
    and the cached-ref checklist below — performed by maintainers under that issue,
    **not** here.
12. **Rotate the secret surface.** Promote a fresh reserve fixture set / new profile
    so the benchmark has an uncontaminated yardstick going forward
    ([#115](https://github.com/tonykoop/makerbench-hwe/issues/115)); the contaminated
    profile remains as a labeled historical record.
13. **Publish the methodology note and update the lifecycle/version labels** so the
    public leaderboard never presents a contaminated board as current.

## Checklists

These are **read-only audit steps**. Record findings in the private incident note;
never copy private values into public channels.

### Public repo exposure
- [ ] Confirm no answer-bearing files exist in the working tree outside
  `private/oracles/` (e.g. no `tasks/*/oracle.scad`).
- [ ] Confirm `git log --all -- 'tasks/*/oracle.scad'` is clean **in the public
  repo** (the [#17](https://github.com/tonykoop/makerbench-hwe/issues/17) acceptance
  criterion); if not, this is a history-level exposure → escalate.
- [ ] Grep public docs/examples/fixtures for accidental gold geometry or seed values.

### Private oracle repo exposure
- [ ] Confirm `makerbench-oracles` visibility and access list are unchanged.
- [ ] Confirm no fork/mirror of the private repo exists publicly.
- [ ] Confirm `makerbench selftest` still resolves oracles and passes (integrity
  tripwire), without publishing any oracle content.

### GitHub cached PR refs / unreachable objects
- [ ] Check whether answer-bearing blobs are reachable via `refs/pull/*`, fork
  networks, or blob/API endpoints even after branch deletion.
- [ ] If reachable, note that branch deletion alone is insufficient — escalate to
  the history/cleanup track ([#17](https://github.com/tonykoop/makerbench-hwe/issues/17)),
  which may require GitHub support to expire cached refs.

### Result-bundle artifacts and dossiers
- [ ] Re-run the bundle preflight (`validate_submission_bundle`) and confirm no
  absolute / parent-traversal / `private/`/`oracles/` artifact paths
  ([`COMMUNITY_SUBMISSION.md`](COMMUNITY_SUBMISSION.md)).
- [ ] Spot-check source artifacts, traces, and dossiers for embedded oracle geometry
  or private fixture data.
- [ ] Identify which committed rows fall inside the contaminated scope; mark, do not
  delete or rescore them.

### Canary reproduction
- [ ] Probe the suspected model for the `makerbench-canary` GUID and for specific
  oracle recall ([`../CANARY.md`](../CANARY.md)).
- [ ] If reproduced, treat as model-training exposure: flag affected leaderboard
  entries and record the probe outcome (not the model's verbatim output) in the note.

### Accidental private detail in public comments / PRs / docs
- [ ] Scan the triggering comment/PR/doc for private paths, held-out seed values,
  official thresholds, or real sensitive SHAs.
- [ ] If present, follow the must-never rules: the fix is to stop the bleeding and
  rotate the exposed secret — **assume anything public was captured** (caches,
  forks, crawlers). Redaction reduces further spread but does not "un-leak."

### Blast-radius decision
- [ ] Family / profile / repo level chosen and written down, with the evidence.
- [ ] When uncertain, size up.

## Must-never-happen rules

- **No silent task edits.** A contaminated task is frozen and relabeled, never
  quietly rewritten to "patch" the leak while keeping its old identity.
- **No silent score reinterpretation.** Old scores keep their original meaning,
  `benchmark_version`, and profile; they are not recomputed or re-explained after
  the fact.
- **No deleting old results to make the board look clean.** History is preserved and
  labeled, never erased.
- **No private fixture paths or details in public comments.** Incident handling
  happens without pasting private paths, seed values, thresholds, or sensitive SHAs
  into public channels.
- **No "fixed" leaderboard without version / profile / lifecycle labeling.** Any
  corrected or replacement board is published with explicit `benchmark_version`,
  `benchmark_profile`, and lifecycle status; it never silently replaces the old one.
- **No claiming contaminated rows as current capability evidence.** A reproduced
  score on a leaked task proves reproduction, not capability, and must never be
  ranked or cited as current.

## Cross-references

- [`PROFILE_LIFECYCLE.md`](PROFILE_LIFECYCLE.md) — the `contaminated` / `retired` /
  `archived` states this playbook drives, and the comparability rules.
- [`VERSIONING.md`](VERSIONING.md) — the preserve-and-relabel result retention policy.
- [`COMMUNITY_SUBMISSION.md`](COMMUNITY_SUBMISSION.md) — bundle preflight,
  verification states, and the audit surface.
- [`TASK_PACKS.md`](TASK_PACKS.md) — the public/private boundary that defines what
  "private" means here.
- [`../CANARY.md`](../CANARY.md) — the contamination canary and training-exposure
  probe.
- Related issues: history scrub ([#17](https://github.com/tonykoop/makerbench-hwe/issues/17)),
  profile lifecycle ([#113](https://github.com/tonykoop/makerbench-hwe/issues/113)),
  reserve fixture promotion ([#115](https://github.com/tonykoop/makerbench-hwe/issues/115)),
  Frontier refresh cadence ([#116](https://github.com/tonykoop/makerbench-hwe/issues/116)),
  release checklist ([`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md),
  [#120](https://github.com/tonykoop/makerbench-hwe/issues/120)).
