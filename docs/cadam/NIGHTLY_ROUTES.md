# Nightly CADAM entrants: choosing the model route (#1102)

A `kind: "cadam"` entrant in the nightly queue takes two optional fields:

```json
{"entrant_id": "cadam-openrouter-fable", "kind": "cadam", "model_id": "anthropic/claude-fable-5.1", "max_cost_usd": 3.0}
{"entrant_id": "cadam-cli-claude", "kind": "cadam", "model_id": "anthropic/claude-sonnet-5.5", "route": "cli-bridge", "runtime": "claude"}
{"entrant_id": "cadam-cli-codex", "kind": "cadam", "model_id": "openai/gpt-6.1-sol", "route": "cli-bridge", "runtime": "codex"}
```

`route` is `openrouter` (default) or `cli-bridge`; `runtime` (`claude` | `codex`) is required for the bridge and refused otherwise.
Give the two routes **different `entrant_id`s**: they are separate entrants, labelled "CADAM · OpenRouter · <model>" and
"CADAM via CLI · <runtime> · <model>" on the morning page (`meta.entrant_label`), and never merged into one score.

## Environment (presence-checked, never printed)

| variable | route | meaning |
| --- | --- | --- |
| `CADAM_BASE_URL` | openrouter | the stock CADAM app (default `http://127.0.0.1:3000`) |
| `OPENROUTER_API_KEY` | openrouter | key for the stock path |
| `CADAM_DIR` | both | the CADAM clone (the picker catalog `src/lib/utils.ts` is read for the model check) |
| `CADAM_BRIDGE_BASE_URL` | cli-bridge | a second CADAM instance started with `CADAM_PROVIDER_OVERRIDE=cli-bridge` (default `http://127.0.0.1:3001`) |
| `CADAM_CLI_BRIDGE_CONFIG` | cli-bridge | bridge config (model -> runtime + CLI model) |
| `CADAM_CLI_BRIDGE_KEY` | cli-bridge | shared bridge key (>= 16 chars) |
| `CADAM_CLI_BRIDGE_CMD` | cli-bridge | optional override of the bridge start command |

## What the runner does

- **Preflight** (`arena preflight`) audits each queued CADAM entrant's route. OpenRouter: key present, model in CADAM's picker.
  CLI bridge: config and key present, model mapped to the entrant's runtime, CLI binary present and logged in
  (`claude auth status` / `codex login status`, no inference), bridge listening or startable, bridge-mode CADAM answering.
- **Run:** a bridge-route entrant starts the bridge before its CADAM turn and stops it afterwards (an already-listening bridge is
  reused and left alone). If the bridge cannot start, that entrant errors without any CADAM request.
- **Provenance** (candidate provenance json): `route`, `runtime`, `cli_version`, `cli_model`, `entrant_label`; the trial row's
  `meta` carries `entrant_label` and `route` for the morning page.

The bridge itself is `tools/cadam_cli_bridge` (#1100); the patched CADAM and how to start both instances is in `LOCAL_SETUP.md` (#1101).
