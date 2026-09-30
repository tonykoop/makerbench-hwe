# Showcase content kit: index

Everything under `docs/showcase/` exists to give the LinkedIn posts real, sourced
assets. This page lists each item, where it came from, what it does **not** show,
and what must be true before it is posted. Epic: #845.

> Status column: **in tree** means the files are on `main`; **PR #N** links the open
> PR that carries them (paths resolve once it merges); **planned** means the story is
> open. Posting states are **postable**, **gated** (a named condition must clear
> first) or **on hold** (blocked on outside work).
>
> Licence column: files we authored (write-ups, scripts, diagrams, our own
> renders) are contributed under the repository's **Apache-2.0** licence (`LICENSE`,
> DCO in `CONTRIBUTING.md`). Where an input is third-party or its status is not
> established, the row says **unknown, gated** rather than assuming one.

## Rules that apply to everything here

- **Evaluation data.** MakerBench is evaluation data: nothing here is training
  material, and no `private/oracles/` content or held-out seed appears.
- **No single-voter Elo.** Only objective scorelines and the arena's
  rank-agreement figure are public; never a preference Elo or vote count.
- **No third-party photo is committed without a clear licence.** Case studies show
  our own generated renders, never the source photograph.
- **Public sources only.** Case studies cite public repos; private repos are not
  named, linked or quoted.
- **Artifacts stay out of the repo.** Run directories live in the gitignored
  `runs/`; only write-ups and chosen renders are committed.
- **Subscription CLIs and `--stub` only.** No metered spend.

## Case studies

| Item | Path / PR | Provenance | Licence and reuse | Posting state |
|---|---|---|---|---|
| Sambuca hero case study (photo to parametric CAD) | [`sambuca/CASE_STUDY.md`](sambuca/CASE_STUDY.md) + two renders in `sambuca/assets/`; [PR #829](https://github.com/tonykoop/makerbench-hwe/pull/829) | Pilot run 2026-07-02 (CADAM with Claude Fable 5, OpenSCAD/BOSL2) from the public sambuca repo's `provenance.json` and `gate.json`; second render is a Round 2 text-only Sonnet export | Write-up: Apache-2.0. The first render was conditioned on a third-party museum photo whose licence is **unresolved** (`CASE_STUDY.md` section 3), so reuse of that render is **unknown, gated**. The photo itself is not committed. | **Gated**: photo licence; unknown turn count |
| Exploratory: instrument CAD from reference images | [`instruments-from-photos/EXPLORATORY.md`](instruments-from-photos/EXPLORATORY.md) + previews; [PR #831](https://github.com/tonykoop/makerbench-hwe/pull/831) | One local Sonnet 5.5 session, unmodified `mesh_objective_gate`, registry brief dimensions | Write-up and our previews: Apache-2.0. Reference images are not committed (one is the owner's own photo, one appears generated, provenance unknown). | **Gated**: exploratory only, not benchmark data; not for a headline |
| Second case study: public-repo instrument | planned ([#851](https://github.com/tonykoop/makerbench-hwe/issues/851)) | | | |

## Posts

All five drafts are in [`linkedin-posts.md`](linkedin-posts.md) ([PR #829](https://github.com/tonykoop/makerbench-hwe/pull/829)), each with a claims-to-source list, corrected against the D2 claims audit. Licence: Apache-2.0 (our text).

| Post | Posting state | Remaining gates |
|---|---|---|
| p1 What I have been building | **Gated** | Answer its open questions ("spare time" is unconfirmed); check ecosystem-diagram freshness |
| p2 Sambuca photo to CAD | **Gated** | Photo licence unresolved (use our render only); no turn count |
| p3 Change one thing at a time | **Gated** | Fill the real-run placeholder from #846/#847 once they merge. The honest content is a tie (model) and a small-cell backend gap, not a ranking |
| p4 Live kora build | **Gated** | Confirm the 28-body count against a screenshot; do not link or imply the private connector |
| p5 New models on the board | **On hold** | Headline waits on the frontier re-run (#668) and #666; the rank-agreement line (rho about 0.07, small rounds) is fixed |

## Matchup assets (post 3)

| Item | Path / PR | Provenance | Licence and reuse | Note |
|---|---|---|---|---|
| Stub demo (mechanism only) | [`post3/`](post3/) ([PR #829](https://github.com/tonykoop/makerbench-hwe/pull/829)) | `--stub` entrants, $0; a demo, not a result | Apache-2.0 | Crop the overlong "Held values" line before using the screenshot |
| Real matchup, model varied | [`post3/matchup-model.md`](post3/matchup-model.md) + `post3/matchup-model/` ([PR #863](https://github.com/tonykoop/makerbench-hwe/pull/863)) | Opus 5.5 vs Sonnet 5.5, OpenSCAD held, ocarina, seeds 0-2, subscription `claude` CLI, 2026-09-30 | Apache-2.0; model-generated designs, no third-party input | A tie (1.000 each); wall time scoped in the report |
| Real matchup, backend varied | [`post3/matchup-backend.md`](post3/matchup-backend.md) + `post3/matchup-backend/` ([PR #864](https://github.com/tonykoop/makerbench-hwe/pull/864)) | OpenSCAD vs CadQuery vs build123d, Sonnet 5.5 held, ocarina, seeds 0-2, 2026-09-30 | Apache-2.0; model-generated designs, no third-party input | 1.000 / 0.778 / 0.556 on one easy task; cause of `watertight` failures unknown |

## Images, demos and tools

| Item | Path / PR | Provenance | Licence and reuse | Posting state |
|---|---|---|---|---|
| Ecosystem diagram (post 1) | [`ecosystem/`](ecosystem/) (SVG, 1200x627 PNG, alt text); [PR #832](https://github.com/tonykoop/makerbench-hwe/pull/832) | Public pieces of the tracker export only | Apache-2.0 (our diagram) | **Gated**: confirm live HF Space and site freshness; counts are as of the export |
| Anonymized render gallery generator | `scripts/generate_render_gallery.py`; [PR #866](https://github.com/tonykoop/makerbench-hwe/pull/866) | Reads arena `run_log.json`; objective scores only | Apache-2.0 | Tool, not a post asset; palette can hint at backend |
| Ecosystem diagram, light/dark asset pack | planned ([#852](https://github.com/tonykoop/makerbench-hwe/issues/852)) | | | |
| 60-second Studio demo GIF | planned ([#850](https://github.com/tonykoop/makerbench-hwe/issues/850)) | | | |

## Adding an item

Add a row here in the same PR. Give the exact command or source that produced it,
the date, the licence or reuse basis (or "unknown, gated"), what it does not show, and
any gate (licence, freshness, an open claim) that must clear before it goes in a post.
