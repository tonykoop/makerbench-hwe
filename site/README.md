# MakerBench leaderboard site

A static, dependency-free website for the MakerBench results. No backend, no
build step required to view it — it reads a single generated JSON and renders
the leaderboard, capability profile, charts, task families, methodology, and a
"run it yourself" guide. Inspired by the clarity of
[DeepSWE](https://deepswe.datacurve.ai/).

```
site/
  index.html          single page (hero · leaderboard · charts · tasks · methodology · run)
  blog/               static methodology / findings articles (no framework)
  assets/app.css      theme + layout (light/dark via [data-theme])
  assets/app.js       fetches data/leaderboard.json and renders everything
  assets/og/          generated SVG social cards for the index + model pages
  data/leaderboard.json  generated artifact (committed, so Pages works with no Python)
  data/archive/       generated per-benchmark_version leaderboard snapshots + index.json
  data/badges/        generated shields.io endpoint JSON for score badges
  models/<slug>/      generated per-model share pages with OG metadata
  build_data.py       aggregator: results/**/*.json -> data/leaderboard.json (+ archive)
```

The only runtime dependency is [Chart.js](https://www.chartjs.org/) loaded from a
CDN. Everything else is vanilla HTML/CSS/JS.

## Regenerate the data

Whenever a new `results.json` is added under the repo's `results/` directory,
rebuild the leaderboard JSON (standard-library Python only, no pip installs):

```bash
python site/build_data.py
```

This scans `results/**/*.json`, groups graded cells by
`(model_identifier, reasoning_level, result_provenance, track, task_id)`,
averages `score` across seeds, and writes:

- `site/data/leaderboard.json`
- `site/data/archive/<benchmark_version>.json` + `site/data/archive/index.json`
- `site/data/badges/*.json`
- `site/assets/og/*.svg`
- `site/models/<badge_slug>/index.html`

It is idempotent — the same inputs produce byte-identical output, so the
committed JSON and generated static files diff cleanly.

## Versioned archived leaderboards

`build_data.py` also writes a versioned snapshot of the leaderboard under
`data/archive/`. Each build refreshes `data/archive/<benchmark_version>.json` (a
self-describing copy of the full payload for the current `benchmark_version`) and
upserts `data/archive/index.json`, which lists the available versions with
comparison metadata (tracks, model count, task families, headline).

`data/leaderboard.json` always stays the default/latest payload; the archive is
purely additive history. Snapshots for **other** versions already on disk are
preserved on rebuild, so bumping `benchmark_version` never drops an older board —
consistent with the result-retention policy in
[`../docs/VERSIONING.md`](../docs/VERSIONING.md). The leaderboard page shows a
version selector (in the table toolbar) when more than one version is archived;
it loads `data/archive/<slug>.json` on demand and falls back silently to
latest-only when no archive index is present.

```bash
python site/build_data.py --archive-dir site/data/archive   # default location
```

Infra-errored cells (an agent that timed out or hit a session limit) are detected
via `grade.notes == "agent_error"`, a failed Level-1 with `checks.agent_ok == false`,
or a detail starting with `agent raised`. They are **excluded from means** and
surfaced separately as `n/a (infra)`.

Optional flags:

```bash
python site/build_data.py --results-dir ../results --registry ../tasks/registry.json --out data/leaderboard.json
```

## Public JSON API

The hosted `data/leaderboard.json` file is a public, fetchable read API for
meta-leaderboards, dashboards, and READMEs:

```js
const res = await fetch("https://tonykoop.github.io/makerbench/data/leaderboard.json");
const leaderboard = await res.json();

for (const model of leaderboard.models) {
  const blind = model.tracks.blind;
  if (blind?.overall_mean != null) {
    console.log(`${model.identifier}: ${blind.overall_mean}/4`);
  }
}
```

Top-level fields:

- `_generated` — human-readable generator note.
- `benchmark_version` — benchmark data version reported by the run files. Paired
  with `benchmark_profile`, it identifies which board a score belongs to; whether
  two scores are comparable also depends on the profile's lifecycle status
  (`core`/`frontier`/`archived`/`retired`/`contaminated`), defined in
  [`../docs/PROFILE_LIFECYCLE.md`](../docs/PROFILE_LIFECYCLE.md).
- `tracks` — track ids present in committed results, such as `blind`.
- `task_families` — ordered task-family metadata from `tasks/registry.json`.
- `capability_axes` — pack-level spider-chart spokes derived from task family
  metadata. Each axis lists its `id`, display `title`, contributing
  `task_family_ids`, `graded_categories`, and summary text.
- `models` — leaderboard rows grouped by model, reasoning level, provenance,
  and track.
- `headline` — one-line summary used by the hero.

Each `models[]` row includes:

- `row_id` — stable JSON identity for
  `(model_identifier, reasoning_level, result_provenance)`.
- `identifier` — model or agent label from the result file.
- `model_family` — display grouping for the spider charts. Known near-term
  identifiers are normalized to exact model labels such as Sonnet 4.6,
  Opus 4.8, Gemini 3.5 Flash, Codex GPT-5.5, and Baseline so thinking and
  effort variants stack together only within the same exact model.
- `reasoning_level` — declared reasoning level, or `null`.
- `result_provenance` — `community` for public dev seeds or `official` for
  held-out maintainer seeds.
- `is_control` — true for deterministic baseline rows.
- `tracks` — per-track aggregate scores.
- `badge_slug` — filesystem-safe id for badges, share pages, and OG cards.
- `badge_endpoint` — relative shields endpoint JSON path.
- `model_page` — relative per-model share page with OG metadata.
- `og_image` — relative per-model SVG social card.

The per-track object contains `overall_mean`, `mean_cost_usd`,
`total_cost_usd`, `mean_wall_time_s`, `total_wall_time_s`,
`mean_agent_call_count`, `mean_retry_count`, `usage_reporting`, `token_usage`,
`n_infra`, `level_histogram`, `has_data`, `families`, `capability_profile`,
`perception`, and `efficiency`. Each family cell is either `null` for untested
or has `mean_score`, `n_seeds`, `n_infra`, and optional `perception` metadata.

Telemetry summaries are additive and should not be used as hidden score inputs.
Cost means and totals include only rows with a numeric structured
`cost.total_cost_usd` or legacy `cost_usd`; missing costs are ignored, not
treated as zero. `usage_reporting` counts rows by `usage.source` so consumers
can distinguish measured token usage from `not_reported` and
`subscription_opaque` runs. `token_usage` sums and means only measured token
fields.

`efficiency` is chart-ready metadata for the score-vs-telemetry frontier chart.
It carries the displayed score, uncertainty fields, seed/family counts, harness
identifier, and four metric slots: `time`, `cost`, `tokens`, and `attempts`.
Missing telemetry uses `value: null` and `available: false`; it is never emitted
as zero. Cost prefers actual measured `mean_cost_usd`, then separately labels
`mean_api_equivalent_usd` as an estimate. Tokens prefer measured `token_usage`,
then separately label `local_log_token_usage` as local-log / opaque-billing
telemetry.

Perception summaries are additive audit metadata. They report counts such as
`n_perception_observations`, `n_render_artifacts`, `n_compiled_observations`,
`warning_count`, and `mean_iterations`; they do not alter geometry means.

`capability_profile` is chart-ready data keyed by `capability_axes[].id`:

```json
{
  "core-3d-print": {
    "mean_score": 3.67,
    "n_families": 1,
    "n_missing": 0,
    "missing_task_family_ids": [],
    "n_infra": 0
  }
}
```

Missing task packs are explicit: `mean_score` is `null`, `n_missing` lists the
gap count, and `missing_task_family_ids` names the absent task families. The
site plots those as blank spider-chart vertices rather than zero-valued scores.

Stability note: consumers should treat this API as additive. Existing field
names are intended to remain stable within a benchmark major version, while new
fields may appear. Use `benchmark_version`, `row_id`, and `badge_slug` instead
of scraping display text.

## Score Badges

`site/build_data.py` emits one
[shields.io endpoint badge](https://shields.io/badges/endpoint-badge) JSON file
per leaderboard row under `data/badges/`.

Example Markdown:

```md
![MakerBench score](https://img.shields.io/endpoint?url=https%3A%2F%2Ftonykoop.github.io%2Fmakerbench%2Fdata%2Fbadges%2Fantigravity-gemini-default-default-or-unset-community.json)
```

Discover badge slugs from either `data/leaderboard.json` (`models[].badge_slug`)
or `data/badges/index.json`.

## Social Cards

The index page emits Open Graph and Twitter card metadata pointing at
`assets/og/leaderboard.svg`. Generated per-model share pages live under
`models/<badge_slug>/` and emit their own `og:image` metadata pointing at
`assets/og/models/<badge_slug>.svg`.

## Failure gallery (curated, not generated)

`data/failure_gallery.json` holds a handful of **curated, public** failure
examples (welded lids, flipped axes, non-manifold meshes, impossible cut paths)
for methodology/launch storytelling. Unlike everything else under `data/`, it is
**hand-authored and committed** — `build_data.py` never reads or writes it, so
regenerating the leaderboard leaves it untouched.

- The launch-facing page `blog/failure-gallery.html` fetches it and renders the
  examples as cards, cross-linking the methodology post and per-task detail
  pages.
- The bundle format, the rule that examples use **candidate/synthetic artifacts
  and public diagnostics only** (never oracle geometry, private thresholds, or
  held-out fixtures), and the maintainer selection workflow are documented in
  [`../docs/FAILURE_GALLERY.md`](../docs/FAILURE_GALLERY.md).
- Curated render placeholders live under `assets/failure-gallery/`.

Validate the bundle (format + privacy guard) before committing changes:

```bash
python site/validate_failure_gallery.py
python -m pytest tests/test_failure_gallery.py -q
```

## Capability Profile Chart

The spider/radar charts are rendered from `capability_axes` plus each row's
per-track `capability_profile`. Each model family gets its own card; variants
inside that family stack together, so future Sonnet rows can compare thinking
enabled/disabled and effort levels such as low, medium, high, xhigh, max, and
ultracode without making unrelated model families visually fight each other.

The current implementation uses pack-level spokes:

- `core-3d-print` -> 3D print DFM
- `catalog-assembly` -> Assembly fasteners
- `sheet-metal` -> Sheet metal
- `laser-2d` -> Laser 2D

Regenerate it with the same site build command:

```bash
python site/build_data.py
```

The charts follow the active Blind/Perception toggle and compare every model
with data on that track, including `baseline-v0`, `claude-code-sonnet`, and
`codex-gpt-5.5` when their committed result files are present.

## View locally

The page fetches `data/leaderboard.json`, which some browsers block over
`file://`. Serve the folder instead:

```bash
cd site
python -m http.server 8000
# open http://localhost:8000/
```

## Deploy to GitHub Pages

Deployment is automated by `.github/workflows/pages.yml`. On every push to `main`
that touches `site/`, `results/`, or the task registry, it regenerates the
leaderboard JSON, badge endpoints, share pages, and OG cards from `results/`,
then publishes `site/` to Pages — so the live site always reflects the committed
runs, with no manual rebuild.

**One-time setup:** in repo *Settings → Pages*, set **Source** to **GitHub
Actions**. After the first run, the live URL appears in the workflow's `deploy`
job summary (and under *Settings → Pages*).

The committed generated files mean the site also works when opened or served
directly without Actions (see *View locally* above); the workflow simply keeps
the published copy fresh.
