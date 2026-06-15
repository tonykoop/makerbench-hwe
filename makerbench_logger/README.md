# makerbench-logger

Drop-in SDK + CLI that records an agent run and emits a schema-valid
[`workflow_manifest.json`](../docs) — the **WorkflowManifest** contract (mb#89)
the MakerBench-HWE *workflow track* and HF Space expect.

**Zero hard dependencies.** Pure standard library, so it installs and runs beside
any agent stack. When the full `makerbench` package is also installed, the SDK
upgrades to authoritative `makerbench.schema.WorkflowManifest` validation
automatically; otherwise it emits the same JSON shape from a local fallback.

## Drop into your agent (5 lines)

```python
from makerbench_logger import WorkflowLogger

with WorkflowLogger(task_id="bracket_v1", seed=0, framework="blender-mcp") as log:
    log.tool_call("blender.add_cube", {"size": 20})              # autonomous  → L0
    log.tool_call("blender.bevel", {"width": 1}, steering="L1")  # human nudge → L1
log.emit("workflow_manifest.json")  # writes the manifest; returns the dict
```

That records every tool-call with its **Human Intervention Index** level, computes
the weighted **autonomy ratio**, captures wall-clock + tool-call count, and writes
a manifest with `stack` / `metrics` / `hii` / `provenance_trace` in the exact
shape `makerbench.schema.WorkflowManifest` (mb#89) expects.

## Human Intervention Index (mb#89)

| Level | Meaning |
|-------|---------|
| `L0`  | Fully autonomous |
| `L1`  | Light NL steering between iterations |
| `L2`  | Heavy copilot / manual geometry edits |

The emitted `hii` block carries per-tier event counts
(`l0_autonomous_events` / `l1_nl_steering_events` / `l2_copilot_manual_events`),
the `highest_level` observed, and a weighted `autonomy_ratio` where an L0 event
contributes full autonomy (1.0), an L1 half (0.5), and an L2 none (0.0) — so
`1.0` means no human in the loop. This weighting and field shape match the
authoritative schema exactly, so the disclosed steering survives the round-trip
into the benchmark (an integrity requirement: a manifest that lost its HII would
silently rank an assisted run as autonomous).

## CLI

Build a manifest from a recorded tool-call log (the `makerbench logger emit` form):

```bash
python -m makerbench_logger emit \
    --tool-call-log calls.json \
    --task-id bracket_v1 --seed 0 \
    -o workflow_manifest.json
```

The log is either a bare list of calls or an object carrying `stack` / `metrics` /
`provenance_trace` plus `tool_call_log`:

```json
[
  {"name": "blender.add_cube", "params": {"size": 20}},
  {"name": "blender.bevel", "params": {"width": 1}, "human_steering": "L1"}
]
```

Flags: `--orchestrator/--framework/--execution-bridge` override stack fields,
`--no-validate` skips schema validation even when `makerbench` is installed, and
`--mbc` also emits bob's `.mbc` certificate (mb#109) when `write_mbc()` is
importable (a no-op otherwise).

## Programmatic extras

```python
log.next_iteration()                          # bump the design/feedback loop counter
log.set_metrics(inference_tokens=4200, estimated_cost_usd=0.06)
log.set_provenance(session_recording_hash="sha256:…", tool_call_log_url="…")
log.add_artifact("out.step")
manifest = log.to_dict()                       # in-memory dict, no file write
```

## Status

`schema_version` `0.1` (SDK `0.2.0`). Tracks mb#92; the WorkflowManifest contract
is owned by mb#89. The SDK emits the authoritative `hii` shape and round-trips
losslessly through `makerbench.schema.WorkflowManifest` — `emit()` raises if the
disclosed steering would not survive validation. A worked end-to-end example
wrapping a real agent stack lives in
[`examples/blender_mcp_stack/`](../examples/blender_mcp_stack/) (mb#93).
