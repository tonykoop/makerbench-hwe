# Code-CAD Arena DoE Orchestration

This document defines the #426 orchestration runner for Epic #421. It scales the
arena from one hand-run comparison to a repeatable design-of-experiments matrix:

`instrument_ids x seeds x reps x model_ids`

## Config

`makerbench.code_cad_orchestrator.OrchestrationConfig` carries:

- instrument ids
- model ids
- seeds
- repetitions
- max attempts per trial
- model-to-provider mapping
- provider rate-limit delays

The runner expands this into stable `trial_id` values, then invokes an injected
trial executor. In the full arena, that executor wires #422 generation and #423
objective scoring; #424 blind voting and #427/#430 analysis consume the run log
later.

## Run Log

`run_orchestration()` writes a JSON run log after every attempted trial. Existing
completed trial rows are skipped on resume, while `error` rows retry until
`max_attempts` is reached. That makes the run log resumable and idempotent.

The emitted manifest is intentionally plain JSON so S3/S4/S6 consumers can read
it without importing provider SDKs:

- S3 objective scoring can attach render/pass-rate payloads per trial.
- S4 blind vote collection can pair candidates by `trial_id`.
- S6 dashboards can aggregate the final model/instrument/seed matrix.

## Rate Limits

Provider rate limits are keyed by provider, not model id. The runner accepts
injected `clock_fn` and `sleep_fn` hooks so production can wait between calls
while tests can prove the schedule without sleeping.
