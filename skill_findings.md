# Cindy B R1 Skill Findings

## Grounding

- Did not run `qmd`; the sprint instruction says it can OOM this machine.
- Treated `/home/tony/hwe-wt/_kit/docs/plans/r1-twingrid/handoffs/sprint-cindy-B.md`
  as the read-only contract.
- Local `#103` references are stale/noisy for an older channel-comparison lane, so
  the handoff scope is the source of truth for this slice.

## Existing Surfaces

- `makerbench/schema.py` already owns the optional `DesignDossier` contract.
- `makerbench/dossier_scoring.py` already scores dossier categories only when a
  task declares them through `dossier_required_categories`.
- `docs/SUBMISSION_CONTRACT.md` is the canonical dossier documentation surface.

## Implemented Slice

- Added optional `DesignDossier.packet` schema objects for GD&T PDF, STL, CNC
  G-code, BOM CSV, sourcing CSV, and `packet_manifest.json`.
- Added the `deliverable_packet` dossier category scorer without adding it to
  current required task categories.
- Added disclosure-grade checks for file roles/hashes, CNC profile/post/tools,
  BOM-line count vs. assembly count, and G-code bounds enclosing part bounds.
- Added `docs/DELIVERABLE_PACKET.md` and linked it from the submission contract.

## Validation

- Passed: `python -m pytest tests/test_dossier_scoring.py tests/test_schema.py`
- Passed: `python -m compileall makerbench tests/test_dossier_scoring.py tests/test_schema.py`
- Passed: `git diff --check`
- Broader registry/site pytest collection was attempted, but local Python is
  missing `typer`, so `tests/test_task_packs.py` could not collect in this
  environment.
