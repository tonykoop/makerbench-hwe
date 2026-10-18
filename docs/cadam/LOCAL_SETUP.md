# Running the local CADAM entrant: two model routes

Epic #1099. The self-hosted CADAM clone (`_meta/CADAM`) can serve the nightly CADAM entrant through two routes. They are
reported as separate entrants ("CADAM · OpenRouter · <model>" vs "CADAM via CLI · <runtime> · <model>") and never merged.
Secrets are never printed or committed: set them in your shell or the clone's git-ignored `.env.local`.

## One-time: bring the clone up to date with our patch set

```bash
scripts/cadam_local_sync.sh --dry-run     # what it would do
scripts/cadam_local_sync.sh               # stash local edits, ff-pull, apply docs/cadam/local-patches/*.diff, npm install
```

It reports a conflict instead of forcing, keeps your stash, and never commits or pushes in the clone. See
`docs/cadam/local-patches/README.md` for what each patch does.

## Route 1: OpenRouter (stock CADAM provider path)

Needs Node 22 (Node 20 fails in `@supabase/realtime-js`: no native WebSocket) and local Supabase up.

```bash
cd _meta/CADAM
# .env.local: OPENROUTER_API_KEY=...   (leave ANTHROPIC_API_KEY unset: patch 0001 then routes anthropic/* via OpenRouter)
npm run dev                                # http://localhost:3000/cadam
```

Default model `anthropic/claude-fable-5.1`.

## Route 2: CLI bridge (subscription quota)

```bash
# 1) the bridge (this repo): Anthropic Messages API over `claude -p` / `codex exec`, loopback + bearer key
export CADAM_CLI_BRIDGE_KEY="$(openssl rand -hex 16)"
cp tools/cadam_cli_bridge/bridge.example.json /tmp/bridge.json     # map model ids -> runtime + CLI model
python -m tools.cadam_cli_bridge --config /tmp/bridge.json         # 127.0.0.1:8787

# 2) CADAM pointed at it
cd _meta/CADAM
CADAM_PROVIDER_OVERRIDE=cli-bridge \
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
ANTHROPIC_API_KEY="$CADAM_CLI_BRIDGE_KEY" \
npm run dev
```

With the override on, `anthropic/*` and `openai/*` model ids go to the bridge verbatim, so every id the picker can send must be a
key in the bridge config (an unmapped id is a 404 from the bridge). Other providers still use their normal route. CADAM's
own title/suggestion helpers that call api.anthropic.com directly are skipped in this mode.

Check the bridge: `curl -s http://127.0.0.1:8787/health` lists the mapped models (no key needed).
