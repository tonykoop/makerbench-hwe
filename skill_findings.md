# skill_findings — iris (A) · R1 · makerbench-logger SDK (mb#92)

Step-0 grounding (NO qmd — read/grep direct).

## What exists today
- `makerbench/schema.py` (pydantic v2, 636 lines) defines `DesignDossier`, `PerceptionObservation`,
  `UsageReport`, `CostReport`, `RuntimeReport`, `Attempt`, `TaskResult`, `RunResults`.
  **There is NO `WorkflowManifest` yet** — that is bob's mb#89, not merged. So the SDK must ship a
  local fallback and import `makerbench.schema.WorkflowManifest` *defensively*.
- Packaging: `pyproject.toml` — pydantic>=2.5, typer, rich; `[project.scripts] makerbench = makerbench.cli:app`;
  `[tool.hatch.build.targets.wheel] packages = ["makerbench"]`.
- CLI: typer app in `makerbench/cli.py` (run/grade/selftest/...).

## Contract sources (issue bodies, read directly via gh)
- **mb#92 (mine):** lightweight logging utility contributors drop into custom Python agents; auto-formats
  tool-calls, prompt timestamps, execution steps into `WorkflowManifest`. Standalone/pip-installable,
  schema-valid output, documented, worked example wrapping a toy agent.
- **mb#89 (bob — WorkflowManifest schema):**
  - `stack` — orchestrator, framework, host_application, execution_bridge (+ versions)
  - `metrics` — wall_clock_seconds, inference_tokens, tool_calls_count, estimated_cost_usd
  - **Human Intervention Index** — L0 fully autonomous / L1 light NL steering between iterations /
    L2 heavy copilot / manual geometry edits
  - `provenance_trace` — tool_call_log_url, session_recording_hash
  - Builds on existing DesignDossier + perception_trace.
- **mb#109 (bob — .mbc certificate):** signed local-grader payload. SDK should *optionally* call
  `write_mbc()` if importable, never hard-depend.

## Design decisions
1. **Zero hard deps / stdlib-only** (argparse, dataclasses, json, hashlib) so it's truly drop-in beside
   any stack. Validation contract is literally `python -m makerbench_logger --help`.
2. **Defensive bridge to makerbench.schema:** if `WorkflowManifest` is present (post bob-merge), validate
   the emitted dict through it; otherwise emit from the local dataclass. Same JSON shape either way.
3. **HII + autonomy ratio** computed from per-tool-call `human_steering` level (L0/L1/L2). autonomy_ratio
   = autonomous_calls / total_calls; overall HII = max level observed.
4. Do NOT touch `makerbench/cli.py` (avoids coupling with alice/bob lanes). Ship `python -m makerbench_logger emit`
   as the `makerbench logger emit` form; document the alias.
