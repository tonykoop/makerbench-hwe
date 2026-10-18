# CADAM routes side-by-side smoke (#1103)

One prompt and the sambuca photo, three routes, graded with the standard checks and laid out in three columns for review:

| | entrant | spends |
| --- | --- | --- |
| a | CADAM · OpenRouter · Fable 5.1 | OpenRouter money - **not hard-capped** (see below) |
| b | CADAM via CLI · claude · best subscription model | claude subscription quota |
| c | CADAM via CLI · codex · GPT-6.1 Sol | ChatGPT subscription quota |

```bash
python scripts/cadam_routes_smoke.py                    # plan + redacted preflight; runs nothing, spends nothing
python scripts/cadam_routes_smoke.py --run --tony-go --yes   # the real run (needs Tony's go); refuses on a NO-GO preflight
```

The OpenRouter route has **no provider-side limit**: the $3 is reserved from the nightly budget and compared after the call, so a long
generation can cost more (the one-generation smoke of #660 cost $0.28). That is why the real run needs `--yes` as well as `--tony-go`.
The CLI columns show "subscription quota" (their ledger charge is $0, not an OpenRouter bill).

Before the real run: local Supabase up; the OpenRouter CADAM instance at `CADAM_BASE_URL`; a second CADAM started with
`CADAM_PROVIDER_OVERRIDE=cli-bridge` at `CADAM_BRIDGE_BASE_URL`; `CADAM_CLI_BRIDGE_CONFIG` / `CADAM_CLI_BRIDGE_KEY` set (see
`NIGHTLY_ROUTES.md`, `LOCAL_SETUP.md`). Override the bridge-mapped models with `--claude-model` / `--codex-model`.

Output: `<out>/<run_id>/index.html` (+ `comparison.json`, each column's `preview.png` and `candidate.scad`, the reference photo) and
`<out>/index.html` pointing at the latest. Default `<out>` is
`StudioPipeline-Pilot/arena-nightly/cadam-routes-smoke/`. The page states that the three are separate entrants, never merged, and
not apples-to-apples (each CLI's own instructions are part of the request). A route that fails still gets its column with the reason.
