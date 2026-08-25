## iris — Logger-SDK (makerbench-hwe · mb#92)
### Why
The drop-in SDK any stack uses to emit a WorkflowManifest — the bridge consumed everywhere.
### Scope
1. `makerbench_logger/` package + CLI: wrap an agent run, log tool-calls (timestamp, params, human-steering), compute HII/autonomy ratio, emit a `WorkflowManifest` JSON. CODE AGAINST bob's mb#89 schema (stub `from makerbench.schema import WorkflowManifest` with a local fallback dataclass if absent).
2. `makerbench logger emit ...` CLI → writes manifest + optionally calls bob's `write_mbc()` if available.
3. README w/ a 5-line "drop into your agent" example.
### Guardrails
Must work standalone (graceful fallback if makerbench.schema absent) so it's pip-installable beside any stack.
### Validation
`python -m makerbench_logger --help`; emit a manifest from a fake tool-call log → valid JSON matching #89.
### Deliverable
PR `feat: makerbench-logger SDK` — `Refs #92`.
