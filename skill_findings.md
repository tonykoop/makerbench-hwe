# iris side B findings

- Did not run `qmd`; the handoff and manager both said it OOMs this box.
- `makerbench.schema` does not yet define `WorkflowManifest`, so
  `makerbench_logger.schema` imports it when present and otherwise uses a local
  v0.1 fallback dataclass aligned to Bob's mb#89 body.
- Bob's local contract body defines the expected manifest areas: `stack`
  (`orchestrator`, `framework`, `host_application`, `execution_bridge`),
  `metrics` (`wall_clock`, `tokens`, `tool_calls`), HII L0/L1/L2, and
  `autonomy_ratio`.
- Existing `docs/TOOL_CONTRACT.md` already defines safe public tool-call trace
  norms: record tool name, public args, bounded result summaries, and never
  include secrets or private/oracle paths.
- The new SDK works standalone for programmatic use without importing
  `makerbench.cli`; only CLI entrypoints require the project `typer` dependency.

Validation:

- `python - <<'PY' ... build_manifest/emit_manifest smoke ... PY` passed and
  confirmed JSON output, tool-call count, `autonomy_ratio`, and secret-param
  redaction.
- `python -m compileall makerbench_logger tests/test_makerbench_logger.py`
  passed.
- `PYTHONPATH=. pytest tests/test_makerbench_logger.py` passed with 2 tests run
  and 2 CLI tests skipped because global Python lacks `typer`; no local venv
  exists in the worktree.
- `python -m makerbench_logger --help` could not run in this shell because
  global Python lacks `typer`.
