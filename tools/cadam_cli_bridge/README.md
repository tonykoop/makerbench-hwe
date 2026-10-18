# CADAM CLI bridge

A localhost-only shim that speaks the Anthropic Messages API (`POST /v1/messages`, JSON and SSE, tool calls)
and answers with `claude -p` (Claude subscription) or `codex exec` (ChatGPT subscription). CADAM points at it
through its existing Anthropic base-URL override (`ANTHROPIC_BASE_URL=http://127.0.0.1:8787`,
`ANTHROPIC_API_KEY=<the bridge key>`). Epic #1099, story #1100. Stdlib only.

```bash
export CADAM_CLI_BRIDGE_KEY="$(openssl rand -hex 16)"      # shared bearer key, >= 16 chars; never commit it
cp tools/cadam_cli_bridge/bridge.example.json /tmp/bridge.json   # edit the model map
python -m tools.cadam_cli_bridge --config /tmp/bridge.json       # 127.0.0.1:8787
curl -s http://127.0.0.1:8787/health                              # no key needed; lists mapped models
```

## What it does

- **Auth / binding:** binds loopback only (a non-loopback `host` is refused); `x-api-key` or `Authorization: Bearer`
  must equal the key (constant-time compare). No prompt, answer or key is ever logged: only method, path, model, status, time.
- **Models:** the request's `model` is looked up in the config (`"anthropic/claude-sonnet-5.5": {"runtime": "claude", "cli_model": "claude-sonnet-5-5"}`);
  unmapped models get a 404 `not_found_error`.
- **claude:** `claude -p --output-format stream-json --input-format stream-json --verbose --tools= --no-session-persistence --model M
  --system-prompt <request system> --strict-mcp-config --mcp-config '{"mcpServers":{}}' --setting-sources "" --disable-slash-commands`.
  Built-in tools, MCP servers, user settings and slash commands are all off (leaving MCP on cost ~220k prompt tokens per call).
  Images are passed as stream-json image blocks.
- **codex:** `codex exec --sandbox read-only --skip-git-repo-check --ephemeral -C <empty scratch dir> -m M -o <file> [-i image...] -- <prompt>`,
  stdin closed. codex has no system-prompt flag, so the request's system text is folded into the prompt. Prompts over ~100 KB go
  through `PROMPT.md` in the scratch dir.
- **Tool calls:** the request's `tools` / `tool_choice` are described in the prompt and the CLI must answer with one JSON object
  `{"text": "...", "tool_calls": [{"name": "...", "input": {...}}]}`. It is validated against the tool names and `tool_choice`,
  then returned as `tool_use` blocks (JSON) or `content_block_start` + `input_json_delta` events (SSE). Bad JSON is retried once
  with the rejection reason, then the client gets a 502 `api_error`.
- **Streaming:** headers and `message_start` go out at once and `ping`s are sent while the CLI runs; the validated answer is then
  replayed as content blocks (the CLIs are not token-streamed). A failure after the headers is an SSE `error` event.
- **Timeouts:** the CLI process group is killed after `timeout_s` and the client gets 504 `timeout_error`. `max_concurrent` CLI runs at a time.

## Known limits

- `usage` is the CLI's own report for claude and a 4-chars-per-token estimate for codex.
- `max_tokens`, `temperature` and `thinking` are accepted and ignored. Each CLI's own instructions are part of the request, so
  results are not apples-to-apples with the OpenRouter route; report them as separate entrants.
- Tests (`tests/test_cadam_cli_bridge.py`) use fake CLIs; nothing there spends quota.
