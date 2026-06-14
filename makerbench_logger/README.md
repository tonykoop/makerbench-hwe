# makerbench-logger

Drop-in SDK for emitting a MakerBench `WorkflowManifest` from an agentic maker
run. The package works before Bob's canonical schema lands: it imports
`makerbench.schema.WorkflowManifest` when available and falls back to a local
v0.1-compatible dataclass otherwise.

```python
from makerbench_logger import RunLogger
logger = RunLogger("run-001", stack={"orchestrator": "codex", "host_application": "Blender"})
parts_search = logger.wrap_tool("parts_search", parts_search)
parts_search(thread="M3")
logger.emit("workflow_manifest.json")
```

CLI:

```bash
python -m makerbench_logger emit --log tool_calls.json --run-id run-001 --out workflow_manifest.json
makerbench logger emit --log tool_calls.json --run-id run-001 --out workflow_manifest.json
```

Tool-call logs may be a JSON list, JSONL, or an object containing `tool_calls`,
`events`, or `trace`. Each event should include `tool` plus `params` or `args`;
`human_intervention_level` accepts `L0`, `L1`, or `L2`.
