# Local CADAM patches

Reviewable patches for the local clone of Adam-CAD/CADAM (`_meta/CADAM`). **Nothing here is sent upstream**
(no PRs to Adam-CAD/CADAM unless Tony asks). Apply in filename order with `scripts/cadam_local_sync.sh`.

| patch | what |
| --- | --- |
| `0001-openrouter-fallback.diff` | `src/server/aiChat.ts`: when `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` is absent, `anthropic/*` and `google/*` models route through OpenRouter (same model-id namespace), so the stock route works with only `OPENROUTER_API_KEY`. |
| `0002-cli-bridge-provider-switch.diff` | `CADAM_PROVIDER_OVERRIDE=cli-bridge` sends `anthropic/*` and `openai/*` models to the CLI bridge (`tools/cadam_cli_bridge`) through the Anthropic provider at `ANTHROPIC_BASE_URL`, with the request's model id verbatim; skips the direct-Anthropic title/suggestion helpers and refuses `createAnthropicText` so the bridge key never goes to api.anthropic.com. |

`BASE` is the upstream commit the patches were cut against (`e339e3a`, "swap Gemini 3.1 Pro for Gemini 4 Argon"). They are
minimal deltas against that tree: `git apply --check` passes on a pristine `git archive` of it, and `tsc --noEmit -p tsconfig.app.json`
and `prettier --check` pass on the patched files. If upstream moves and a patch stops applying, the sync script stops and says which.

## Why these replace the old uncommitted `aiChat.ts` edit

The clone's uncommitted/staged `src/server/aiChat.ts` was a whole-file copy from before upstream's model-capabilities refactor
(`modelCapabilities.ts`, the Fable 5.1 / Opus 5.5 / Sonnet 5.5 price entries). Against current upstream it reverts those changes
(about -167/+107 lines) instead of expressing only the fallback. 0001 is the same fallback as a small delta on current upstream.
The sync script stashes the old edit (it is kept in the stash, never dropped) and applies these instead.
