# Entrant tools: measure and render for studio-tier entrants (design, R-5 #798)

> Status: **design only**. Nothing in this note is wired into an entrant yet.
> R-5 builds it with stub entrants; any live run is Tony-gated.

## Why

Frontier CAD agents do better when they can inspect their own output: render
it, cut a section, measure a wall. #793 adds the helpers: `makerbench.measure`
(R-1, #794) and `makerbench.render_view` (R-2, #795). R-5 exposes them to
entrants in the **studio** tier as read-only tools, without weakening
confinement or benchmark integrity.

## Tool contract

Two tools. Both take the candidate **source text** as an argument, never a path.

| Tool | Input | Output |
|---|---|---|
| `measure` | `backend` (`openscad`, `cadquery`), `source`, optional `sections[]` (`axis`+`offset_mm`, or `origin`+`normal`), optional `bodies` pair for clearance | JSON: `measure_candidate` report (volume, bbox, min wall, sections, clearance), each entry `ok`/`error` |
| `render_view` | `backend`, `source`, optional `views[]` (names from `VIEW_SET`), optional `sections[]` | PNGs of the fixed view set and section cuts plus the `RenderViewResult` JSON (sha256, `cut_area_mm2`) |

Rules the implementation must keep:

1. **Source in, results out.** The tool writes the source into a fresh temp dir
   it owns, compiles it through the sandboxed compiler (`scad_sandbox` for
   OpenSCAD, the Bubblewrap CadQuery backend), measures or renders, and
   returns the result. It never accepts a filesystem path from the entrant, so
   it has nothing outside the workspace to read.
2. **No host execution.** Every compile and render runs in Bubblewrap. If the
   sandbox is unavailable the tool returns an error. It never falls back to a
   host `openscad` or host CadQuery.
3. **Nothing written outside the tool's temp dir.** The trial's artifacts,
   `run_log.json`, `runs/` and `private/` are untouched. The tool's own temp dir
   is deleted after the call; images are returned inline (base64) or copied
   into the trial's staged workspace scratch, never into the run dir.
4. **Budgeted.** A per-trial cap on tool calls (proposal: 12) and on total tool
   wall time (proposal: 180 s), counted separately from the entrant's
   `--max-turns`. Over budget, the tool returns `budget exhausted` and the
   entrant continues without it.
5. **Deterministic.** The same source and arguments give the same JSON and the
   same image sha256, since R-1 and R-2 are seeded and use a fixed camera set.
   Provenance can therefore store hashes instead of images.

## Tiers

| Tier | Tools |
|---|---|
| `blind` | **None.** The Claude command keeps `--tools=` and gets no MCP config; codex/agy get no tool server. A test must prove this. |
| `packet`, `repo`, `image` | None in R-5. They stay comparable to their existing rounds. |
| `studio` | `measure`, `render_view` |

Studio scores are already reported per tier and never merged into blind Elo
(`docs/ARENA_PHILOSOPHY.md`). A tool-enabled studio round is a **different
treatment** from a tool-less studio round, so it is recorded as such (see
provenance) and compared with `arena compare-tiers`, never pooled.

## Transport per provider

### Claude

Today's studio command is `claude -p … --tools=Read,Glob,Grep --restricted
--strict-mcp-config --permission-mode dontAsk` with cwd at the staged workspace.

Proposal: a small stdio MCP server, `python -m makerbench.entrant_tools_mcp`,
passed with `--mcp-config <generated.json>` alongside `--strict-mcp-config`, so
it is the only MCP server loaded. Its two tool names are added to the allow-list.

**To verify in R-5 before relying on it:** whether `--tools=` restricts MCP
tools, or whether they must be named explicitly (for example
`mcp__makerbench__measure`) in an allow-list. Also, whether `--restricted`
leaves MCP stdio servers enabled. The 2026-09-14 finding that the `=` form of
`--tools` is required (variadic flag) suggests pinning the exact argv in a unit
test, the way `_CLAUDE_READ_ONLY_TOOLS` is pinned today. These are CLI-behaviour
checks and can't be settled by reading code; they need one Tony-gated live probe.

### Codex and agy (inside #787's Bubblewrap)

The entrant CLI runs inside `entrant_sandbox`, with an allow-list mount
namespace and a shared network namespace. Running the tool server *inside*
that sandbox would need nested Bubblewrap for the compile sandbox, and nested
unprivileged user namespaces are not reliably available. So:

* **Proposal:** the tool server runs **on the host, outside the entrant
  sandbox**, as the parent of nothing the entrant controls. It is reachable
  through a **Unix socket bind-mounted into the entrant sandbox** (one extra
  `--bind <socket> /run/makerbench-tools.sock`). The entrant's MCP config
  points at a tiny stdio shim, bundled read-only under `/opt/makerbench-entrant`,
  that forwards JSON-RPC over that socket. The host server compiles in its own
  Bubblewrap as in rule 2.
* This keeps the entrant's filesystem view unchanged. The only new thing
  visible inside the sandbox is one socket that accepts `measure` and
  `render_view` requests with source-text arguments.
* A provider whose tool server can't be attached this way reports the tools as
  unavailable, and the trial still runs tool-less, recorded so. Its
  confinement classification (`entrant_confinement`) is unchanged by tools:
  tools never turn `unconfined` into `verified`.

## What provenance records

Each trial's provenance JSON (`gen/<trial_id>.provenance.json`) and the trial
`result` gain:

```json
"tools": {
  "enabled": ["measure", "render_view"],
  "transport": "claude-mcp-stdio" | "socket-shim" | "none",
  "budget": {"max_calls": 12, "max_wall_s": 180},
  "calls": [
    {"seq": 1, "tool": "measure", "backend": "openscad",
     "source_sha256": "…", "args_sha256": "…",
     "ok": true, "error": null, "result_sha256": "…",
     "image_sha256": [], "wall_s": 4.2}
  ],
  "calls_used": 3, "budget_exhausted": false
}
```

* Blind trials record `"tools": {"enabled": [], "transport": "none"}`, so the
  absence is explicit.
* Hashes, not source or images: the final candidate is already stored, and
  intermediate drafts can be large. `source_sha256` still lets a reviewer tell
  whether the entrant measured the candidate it finally submitted.
* The objective scoreline row stays unchanged. A tools-on round is identified
  by its run config (`tools_enabled`) and by `tools.enabled` on each trial.

## Confinement tests R-5 needs

Each test must go RED when its guard is removed, like #789, #790 and #787.
Stub entrants only.

1. **Blind tier gets no tools.** The built Claude argv for `blind` contains
   `--tools=` and no `--mcp-config`. The codex/agy sandbox argv for `blind`
   binds no tool socket. RED: pass tools unconditionally.
2. **Studio argv is exact.** The Claude studio argv contains the tool names in
   `=` form, `--strict-mcp-config`, and only the generated MCP config. RED:
   drop `--strict-mcp-config`.
3. **No path arguments.** A `measure` call whose `source` is an `import("/etc/passwd")`
   / `include <…host path…>` (OpenSCAD) or `open("/etc/hostname")` (CadQuery)
   returns a compile error or an empty mesh, and the host file's content never
   appears in the response. The pattern is #790's host-read tests. RED: compile
   on the host.
4. **Tool server writes nothing outside its temp dir.** The run dir, the
   workspace (except the declared scratch) and a sentinel file stay unchanged
   (compare sha256 before and after). RED: write the image into the run dir.
5. **Sandbox unavailable ⇒ tool error, no host process.** `sandbox_available`
   is monkeypatched false and `subprocess.run`/`Popen` are tripwires. RED:
   host fallback.
6. **Budget.** Call 13 returns `budget exhausted` and does not compile. RED:
   remove the cap.
7. **Socket is the only new mount.** For codex/agy the sandbox argv differs
   from the tool-less argv by exactly one `--bind <socket> /run/makerbench-tools.sock`.
   RED: bind the socket's parent directory.
8. **Provenance is complete.** A stub entrant making N calls yields N `calls`
   entries with matching `source_sha256`. Blind yields `enabled: []`. RED: skip
   recording.
9. **Tools never upgrade confinement.** `entrant_confinement(model, "studio",
   sandboxed=False)` for codex stays `unconfined` with tools on. RED: mark tool
   trials verified.

## Out of scope for R-5

* Live model runs (Tony-gated).
* Letting entrants write files or run arbitrary code: the tools are fixed
  functions over source text.
* Tools in the blind, packet, repo or image tiers.
