# Correction-Leverage column (Issue #297, round2)

## Scope

Add a leaderboard column for the correction delta between Perception and Blind tracks.

## Metric

`correction_delta = perception_score - blind_score`

## Behavior

- Compute per workflow entry from existing Perception and Blind values.
- Render as a sortable leaderboard column.
- Visually distinguish:
  - positive delta (self-correcting),
  - zero/negative delta (non-improving or regressing).
- Preserve provenance links to the workflow record.

## Data handling

Source values remain the existing Perception and Blind score fields.

## Acceptance

Issue #297 is complete when Δ is computed consistently and visible as a practical filter for workflow self-correction.
