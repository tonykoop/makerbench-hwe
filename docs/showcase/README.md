# Showcase content kit: index

Everything under `docs/showcase/` exists to give the LinkedIn posts real, sourced
assets. This page lists each item, where it came from, what it does **not** show,
and what must be true before it is posted. Epic: #845.

> Status column: **in tree** means the files are on `main`; **PR #N** means they
> land when that PR merges (this index is written against the open PRs, so links
> to those paths resolve after the merges); **planned** means the story is open.

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

| Item | Path | Status | Provenance | Not shown / gate before posting |
|---|---|---|---|---|
| Sambuca hero case study (photo to parametric CAD) | [`sambuca/CASE_STUDY.md`](sambuca/CASE_STUDY.md) + two renders in `sambuca/assets/` | PR #829 | Pilot run 2026-07-02 (CADAM with Claude Fable 5, OpenSCAD/BOSL2), sourced from the public sambuca repo's `provenance.json` and `gate.json`; the second render is a Round 2 text-only Sonnet export | One run, unknown turn count. Fails the provisional wall-thickness gate. **The source museum photo's licence is unresolved and the photo is not committed**; get written permission or keep to renders. |
| Exploratory: instrument CAD from reference images | [`instruments-from-photos/EXPLORATORY.md`](instruments-from-photos/EXPLORATORY.md) + previews | PR #831 | One local Sonnet 5.5 session, unmodified `mesh_objective_gate`, registry brief dimensions | Exploratory, not benchmark data; not comparable to the sambuca pilot; sources deliberately not committed |
| Second case study: public-repo instrument | planned (#851) | planned | | |

## Posts

| Item | Path | Status | Provenance | Gate before posting |
|---|---|---|---|---|
| Post drafts p1-p5 with claims-to-source lists | [`linkedin-posts.md`](linkedin-posts.md) | PR #829 | Corrected against the D2 claims audit | p1/p2/p4 need their listed open questions answered; p3 needs the real matchup (below); p5 headline is on hold for #668 |
| Post 3 stub demo (mechanism only) | [`post3/`](post3/) | PR #829 | `--stub` entrants, $0; **a demo of the mechanism, not a result** | Crop the overlong "Held values" line in the screenshot |
| Post 3 real matchup, model varied | [`post3/matchup-model/`](post3/matchup-model/) | PR #863 | Opus 5.5 vs Sonnet 5.5, OpenSCAD held, ocarina, seeds 0-2, subscription `claude` CLI | **A tie** (1.000 vs 1.000); do not claim either model is better |
| Post 3 real matchup, backend varied | [`post3/matchup-backend/`](post3/matchup-backend/) | PR #864 | OpenSCAD vs CadQuery vs build123d, Sonnet 5.5 held, ocarina, seeds 0-2 | 1.000 / 0.778 / 0.556 on one easy task; cause of `watertight` failures unknown; not a verdict on the CAD systems |

## Images and demos

| Item | Path | Status | Provenance | Gate before posting |
|---|---|---|---|---|
| Ecosystem diagram (post 1) | [`ecosystem/`](ecosystem/) (SVG, 1200x627 PNG, alt text) | PR #832 | Built from the public pieces of the tracker export only | Check live Space and site freshness first; counts are as of the export |
| Ecosystem diagram, light/dark asset pack | planned (#852) | planned | | |
| Anonymized render gallery generator | planned (#849) | planned | | |
| 60-second Studio demo GIF | planned (#850) | planned | | |

## Adding an item

Add a row here in the same PR. Give the exact command or source that produced it,
the date, what it does not show, and any gate (licence, freshness, an open claim)
that must clear before it goes in a post.
