# bob side B findings

- Did not run `qmd`; the sprint handoff explicitly warned that it OOMs this box.
- Used the read-only handoff at `/home/tony/hwe-wt/_kit/docs/plans/r1-twingrid/handoffs/sprint-bob-B.md` as the active contract.
- Searched local sprint kit files for mb#89/mb#109 context. No separate cached issue bodies were present beyond the bob body/pack text, which matched the handoff.
- Existing implementation surface was concentrated in `makerbench/schema.py`, with schema round-trip coverage in `tests/test_schema.py`.
- Implemented an additive `WorkflowManifest` contract that reuses existing `DesignDossier`, `PerceptionObservation`, and `ArtifactFile` types instead of changing their behavior.
- Added Human Intervention Index levels `L0`/`L1`/`L2`, an `autonomy_ratio`, workflow stack metadata, workflow metrics, JSON Schema export, and HMAC-backed `.mbc` write/verify helpers.
- Added focused tests for manifest round-trip/schema export and `.mbc` write/verify/tamper detection.
- Validation run: `python3 -m pytest tests/test_schema.py` passed. Plain `pytest tests/test_schema.py` failed before collection because that entrypoint did not put the worktree package on `sys.path`.
- Adjacent validation attempt `python3 -m pytest tests/test_schema.py tests/test_submission.py tests/test_attestation.py tests/test_regrade.py` was blocked during collection by missing local dependencies (`typer`, `shapely`); `tests/test_schema.py` itself had already passed in that invocation.
