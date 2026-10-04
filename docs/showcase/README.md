# Showcase index

`docs/showcase/` holds MakerBench case studies, controlled matchups, render
galleries, diagrams and demos, each built from real runs and public sources. This
page lists each item, where it came from, what it does **not** show, and what must
be true before it is reused publicly. Epic: #845.

> Status column: **in tree** means the files are on `main`; **PR #N** links the open
> PR that carries them (paths resolve once it merges); **planned** means the story is
> open. Reuse states are **ready**, **gated** (a named condition must clear
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

| Item | Path / PR | Provenance | Licence and reuse | Reuse state |
|---|---|---|---|---|
| Sambuca hero case study (photo to parametric CAD) | [`sambuca/CASE_STUDY.md`](sambuca/CASE_STUDY.md) + two renders in `sambuca/assets/`; [PR #829](https://github.com/tonykoop/makerbench-hwe/pull/829) | Pilot run 2026-07-02 (CADAM with Claude Fable 5, OpenSCAD/BOSL2) from the public sambuca repo's `provenance.json` and `gate.json`; second render is a Round 2 text-only Sonnet export | Write-up: Apache-2.0. The first render was conditioned on a third-party museum photo whose licence is **unresolved** (`CASE_STUDY.md` section 3), so reuse of that render is **unknown, gated**. The photo itself is not committed. | **Gated**: photo licence; unknown turn count |
| Exploratory: instrument CAD from reference images | [`instruments-from-photos/EXPLORATORY.md`](instruments-from-photos/EXPLORATORY.md) + previews; [PR #831](https://github.com/tonykoop/makerbench-hwe/pull/831) | One local Sonnet 5.5 session, unmodified `mesh_objective_gate`, registry brief dimensions | Write-up and our previews: Apache-2.0. Reference images are not committed (one is the owner's own photo, one appears generated, provenance unknown). | **Gated**: exploratory only, not benchmark data; not for a headline |
| Second case study: stave djembe, reference photo to parametric CAD | [`djembe/CASE_STUDY.md`](djembe/CASE_STUDY.md) + one render; [PR #868](https://github.com/tonykoop/makerbench-hwe/pull/868) | One Sonnet 5.5 image-tier run, seed 0, 2026-09-30; repo `tonykoop/djembe` (public) | Write-up and render: Apache-2.0. Repo licence CC BY 4.0. The reference photo is **not committed** (shows a person, GPS metadata). | **Gated**: one run, `min_wall` failed (cause unknown); do not show the photo uncropped |
| Case study 3: kora, reference photo to parametric CAD (manager-approved substitution) | [`kora/CASE_STUDY.md`](kora/CASE_STUDY.md) + reference photo copy and six renders in `kora/assets/`; [PR #924](https://github.com/tonykoop/makerbench-hwe/pull/924) | Sonnet 5.5, photo vs brief-only, seeds 0-2, subscription CLI, 2026-09-30; public repo `tonykoop/kora` | Write-up and renders: Apache-2.0. Photo is the repo owner's own, under the repo's CC BY 4.0 (photographer assumed); committed only as a 900 px metadata-stripped copy, credit `tonykoop/kora`. Third-party marketplace photos in that repo were not used. | **Gated**: the mesh checks pass on rough designs so scores say little, and the one failure is a `min_wall` result pending calibration (#905); photo influence unknown (weakly suggestive, n=3) |

## Matchups (vary one axis)

| Item | Path / PR | Provenance | Licence and reuse | Note |
|---|---|---|---|---|
| Stub demo (mechanism only) | [`post3/`](post3/) ([PR #829](https://github.com/tonykoop/makerbench-hwe/pull/829)) | `--stub` entrants, $0; a demo, not a result | Apache-2.0 | Crop the overlong "Held values" line before using the screenshot |
| Real matchup, model varied | [`post3/matchup-model.md`](post3/matchup-model.md) + `post3/matchup-model/` ([PR #863](https://github.com/tonykoop/makerbench-hwe/pull/863)) | Opus 5.5 vs Sonnet 5.5, OpenSCAD held, ocarina, seeds 0-2, subscription `claude` CLI, 2026-09-30 | Apache-2.0; model-generated designs, no third-party input | A tie (1.000 each); wall time scoped in the report |
| Real matchup, backend varied | [`post3/matchup-backend.md`](post3/matchup-backend.md) + `post3/matchup-backend/` ([PR #864](https://github.com/tonykoop/makerbench-hwe/pull/864)) | OpenSCAD vs CadQuery vs build123d, Sonnet 5.5 held, ocarina, seeds 0-2, 2026-09-30 | Apache-2.0; model-generated designs, no third-party input | Original run 1.000 / 0.778 / 0.556 on one easy task; an S5 update section on `main` shows the `watertight` failures were partly a gate artifact (sliver triangles, fixed) and a re-run scoring 1.000 for all three. Quote the update, not the original table |

## Strings matchups (epic S6, boat-shaped harp)

All from the `sambuca` brief (public repo `tonykoop/sambuca`), Sonnet 5.5 / Opus 5.5 / GPT-6.1 Sol through subscription CLIs, seeds 0-2, 2026-09-30, Apache-2.0, objective checks only. **Every pass rate in this section depends on the `min_wall` check, which the S7 analysis (#900, [PR #905](https://github.com/tonykoop/makerbench-hwe/pull/905)) finds flips with its random sample seed: treat all of these scores as provisional and gated until it is calibrated.**

| Item | Path / PR | Provenance | Reuse state |
|---|---|---|---|
| Inventory of public string briefs | [`strings/README.md`](strings/README.md) ([PR #887](https://github.com/tonykoop/makerbench-hwe/pull/887), merged) | `--stub` over 20 public briefs; one Sonnet 5.5 trial on six | Reference |
| Context varied (blind vs image) | [`strings/matchup-context.md`](strings/matchup-context.md) ([PR #891](https://github.com/tonykoop/makerbench-hwe/pull/891), merged) | Sonnet 5.5 + OpenSCAD held | **Gated**: scores provisional; image-tier renders also gated on the unresolved photo licence |
| Model varied (Opus / Sonnet / Sol) | [`strings/matchup-model.md`](strings/matchup-model.md) ([PR #896](https://github.com/tonykoop/makerbench-hwe/pull/896), merged) | OpenSCAD held; includes arena retries after 120 s compile timeouts | **Gated**: scores provisional; not a ranking |
| Backend varied (OpenSCAD vs CadQuery) | `strings/matchup-backend.md` ([PR #897](https://github.com/tonykoop/makerbench-hwe/pull/897), open) | Sonnet 5.5 held; replay in another runtime scored one seed differently, cause unknown | **Gated**: scores provisional; not yet on `main` |
| min_wall analysis | `strings/min-wall-analysis.md` ([PR #905](https://github.com/tonykoop/makerbench-hwe/pull/905), open, other lane) | Read-only measurement of the local run dirs | Reference; decides the calibration |
| Anonymized strings gallery | [`strings/gallery/`](strings/gallery/) ([PR #898](https://github.com/tonykoop/makerbench-hwe/pull/898), merged) | 18 blind-tier designs via `generate_render_gallery.py`; nine share one setup | **Gated**: scores shown on cards are provisional; image-tier renders excluded |

## Images, demos and tools

| Item | Path / PR | Provenance | Licence and reuse | Reuse state |
|---|---|---|---|---|
| Ecosystem diagram | [`ecosystem/`](ecosystem/) (SVG, 1200x627 PNG, alt text); [PR #832](https://github.com/tonykoop/makerbench-hwe/pull/832) | Public pieces of the tracker export only | Apache-2.0 (our diagram) | **Gated**: confirm live HF Space and site freshness; counts are as of the export |
| Anonymized render gallery generator (HTML + `grid.png`) | `scripts/generate_render_gallery.py`; [PR #866](https://github.com/tonykoop/makerbench-hwe/pull/866) | Reads arena `run_log.json`; neutral labels, objective scores only, PNG metadata stripped | Apache-2.0 | Tool; render palette can still hint at the backend |
| Ecosystem diagram, light/dark asset pack + alt text | same folder, `ecosystem-{light,dark}` and `alt-text.txt`; [PR #869](https://github.com/tonykoop/makerbench-hwe/pull/869) (stacked on #832) | Palette swap of the #832 diagram | Apache-2.0 (our diagram) | **Gated** as above |
| 60-second Studio demo GIF | [`studio-demo/`](studio-demo/) + `scripts/record_studio_demo.py`; [PR #867](https://github.com/tonykoop/makerbench-hwe/pull/867) | Scripted Playwright walkthrough over the real #846/#847 run dirs; no vote screens | Apache-2.0 | **Gated**: crop or accept the overlong "Held values" line and the default voter name in the header |

## Adding an item

Add a row here in the same PR. Give the exact command or source that produced it,
the date, the licence or reuse basis (or "unknown, gated"), what it does not show, and
any gate (licence, freshness, an open claim) that must clear before it is reused publicly.
