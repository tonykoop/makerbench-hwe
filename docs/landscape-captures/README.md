# Landscape capture docs

Drop location for the capture-cluster outputs (competitive scans, competitor
teardowns, prior-art writeups) that feed the site's "Benchmark of Benchmarks"
landscape page. Issue [#674](https://github.com/tonykoop/makerbench-hwe/issues/674)
wired this directory into the landscape data pipeline; the capture issues
themselves are [#610](https://github.com/tonykoop/makerbench-hwe/issues/610)
(AI-CAD competitive scan), [#611](https://github.com/tonykoop/makerbench-hwe/issues/611)
(ty\e teardown), and [#612](https://github.com/tonykoop/makerbench-hwe/issues/612)
(Phoenix-bench / agentic-sim prior art).

## How it works

`site/landscape_data.py` (the only writer of `site/data/landscape.json`) scans
this directory for `*.md` files (this README excluded) and emits a `captures`
array into the JSON. The landscape page (`site/assets/landscape.js`) renders
that array as a "Capture docs" list with links back to the documents here.
After adding or editing a capture doc, regenerate and commit the JSON:

```
python site/landscape_data.py
```

## File convention

- **Name:** `<issue>-<slug>.md`, e.g. `611-tye-teardown.md`. The leading
  number is the capture issue it lands; it becomes the `issue` field in the
  JSON. A file without a leading number is still ingested (issue omitted).
- **Title:** the first `# ` heading is the display title (falls back to the
  filename stem).
- **Summary:** the first non-heading paragraph becomes the one-line summary
  shown on the site (collapsed whitespace, truncated to ~280 chars).
- **Content rules:** these docs are served from the PUBLIC repo. Primary-source
  links and published claims only — no private run data, no personal paths,
  no unpublished contact info scraped from feeds.

## Status

No capture docs have landed yet — #610/#611/#612 are still in flight
(lane-W6). The pipeline ingests whatever is present here; an empty directory
yields an empty `captures` array and the site section stays hidden.
