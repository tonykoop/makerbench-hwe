# Claude + Blender MCP reference stack

A turnkey, cloneable **Starter Stack** for the MakerBench-HWE *workflow track*
(issue #93). Clone it, run `docker compose up`, and watch Claude (or any MCP
client) drive a local Blender scene graph over a socket — emitting a
benchmark-gradeable STL plus a schema-valid `workflow_manifest.json` (mb#89/#92).

This is the open-source baseline for the `assisted-workflow` league's
`api-driven-code` provenance lane: the artifact is hard-graded by geometry; the
process is disclosed by the manifest.

## Architecture

```
  Claude / MCP client
        │  MCP (stdio)
        ▼
  mcp_server/server.py          ── any Python process; logs every tool-call
        │  TCP / JSON-RPC 2.0 (:8765)
        ▼
  blender_addon/mb_blender_bridge.py   ── runs INSIDE headless Blender
        │  bpy  (main thread only, via bpy.app.timers queue)
        ▼
  Blender scene graph  ──►  vented_plate.stl  (gradeable artifact)
```

`bpy` only exists inside Blender's own interpreter, so the agent can't import it
directly — it drives Blender through the socket bridge. The bridge speaks
newline-delimited JSON-RPC 2.0 and mirrors a small set of scene ops
(`get_scene_info`, `clear_scene`, `add_cube`, `add_cylinder`,
`boolean_difference`, `set_transform`, `export_stl`, `render`).

### The `bpy` thread-safety pattern (the #93 stressor)

Blender's data API is **not thread-safe** and may only be touched from the main
thread. The socket server runs on a background thread, so every request that
reads or mutates scene data is *queued* and executed on the main thread via
`bpy.app.timers.register`; the worker thread blocks on a `threading.Event` until
the main thread returns the result. Multi-step boolean edits driven from an
external agent therefore never race the scene graph. Getting this wrong is a
natural benchmark stressor — a stack that mishandles it crashes on the sample
task's boolean sequence.

## One-command bring-up

```bash
cd examples/blender_mcp_stack
docker compose up --build
```

This starts two services:

| Service   | What it does |
|-----------|--------------|
| `blender` | Headless Blender (`linuxserver/blender`) running the bridge on `:8765`. No GPU required — the sample task exports geometry; rendering is optional. |
| `agent`   | Runs the sample **vented-plate** task, drives the bridge, and writes `./out/vented_plate.stl` + `./out/workflow_manifest.json`. |

When `agent` exits, check `./out`:

```
out/
├── vented_plate.stl          # feed to the MakerBench grader
└── workflow_manifest.json    # submit as the workflow-track row (mb#89)
```

## Run the sample task against an existing bridge

If you already have headless Blender + the bridge running (locally or in the
compose `blender` service), run the task directly:

```bash
python tasks/vented_plate_demo.py --host 127.0.0.1 --port 8765 --out ./out
```

To run the Blender side without Docker:

```bash
blender --background --python blender_addon/headless_launcher.py
```

## What the sample task builds

`tasks/vented_plate_demo.py` procedurally builds an 80×60×4 mm mounting plate
with four corner bolt holes and a central vent — each hole/vent is a cylinder
boolean-differenced out of the plate. The vent cut is recorded as **L1**
(light NL steering: a human design choice nudged in), so the emitted manifest
discloses a non-autonomous run. Everything else is **L0** (autonomous).

The emitted manifest looks like (abridged):

```json
{
  "schema_version": "0.1",
  "task_id": "vented_plate",
  "seed": 0,
  "stack": {
    "orchestrator": {"name": "claude-code", "version": null},
    "framework": {"name": "makerbench-logger", "version": null},
    "host_application": {"name": "blender", "version": null},
    "execution_bridge": {"name": "blender-mcp", "version": null}
  },
  "metrics": {"tool_calls_count": 12, "wall_clock_seconds": 1.8},
  "hii": {"l0_autonomous_events": 11, "l1_nl_steering_events": 1,
          "l2_copilot_manual_events": 0, "autonomy_ratio": 0.958, "highest_level": "L1"},
  "provenance_trace": {"tool_call_log_url": null, "session_recording_hash": null}
}
```

That `hii` block is the authoritative mb#89 shape — it round-trips losslessly
through `makerbench.schema.WorkflowManifest`, so your disclosed steering is never
silently collapsed to "fully autonomous".

## Wire Claude to it (MCP)

`mcp_server/server.py` registers each bridge method as an MCP tool. With the
`mcp` package installed (`pip install mcp`), point Claude Desktop / Claude Code at:

```json
{
  "mcpServers": {
    "makerbench-blender": {
      "command": "python",
      "args": ["-m", "server"],
      "cwd": "examples/blender_mcp_stack/mcp_server",
      "env": {"MB_BRIDGE_HOST": "127.0.0.1", "MB_BRIDGE_PORT": "8765"}
    }
  }
}
```

For a *logged* session that emits a manifest, drive Blender through
`server.BlenderSession` (see the sample task) rather than the raw tools — the
session records each call's HII level and emits `workflow_manifest.json` on exit.

## Submitting to the leaderboard

1. Grade `vented_plate.stl` with the MakerBench grader (see the repo root README).
2. Submit `workflow_manifest.json` as the workflow-track row. Workflow rows live
   in the **Workflows** league and cap at artifact-verified (`public-regrade-verified`)
   — never ranked head-to-head against the Autonomous league (mb#90).

## Files

| Path | Role |
|------|------|
| `blender_addon/mb_blender_bridge.py` | JSON-RPC scene bridge + bpy main-thread queue (runs in Blender). |
| `blender_addon/headless_launcher.py` | `blender --background --python` entrypoint. |
| `mcp_server/bridge_client.py` | Stdlib JSON-RPC client for the bridge. |
| `mcp_server/server.py` | MCP tool registry + logged `BlenderSession`. |
| `tasks/vented_plate_demo.py` | Sample task → gradeable STL + manifest. |
| `Dockerfile.blender`, `Dockerfile.agent`, `docker-compose.yml` | One-command bring-up. |

Tests for the wiring (no Docker/Blender needed) live in
`tests/test_blender_mcp_stack.py` at the repo root.
