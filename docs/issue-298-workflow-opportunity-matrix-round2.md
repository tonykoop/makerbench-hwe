# Workflow Opportunity Matrix (Issue #298, round2)

## Scope

Build a workflow × hard-domain matrix showing Level-4 unlock strength.

## Layout

- Rows: workflows
- Columns: hard domains (`vented_plate`, multi-part assembly, `sheet_metal_bracket`, etc.)
- Cell semantics:
  - locked/unavailable,
  - tested failure,
  - Level-4 success with strength/intensity.

## Visual behavior

- Heatmap styling mirrors existing opportunity-matrix patterns.
- Empty/untested cells are distinct from explicit failures.
- Clear tooltip legend for levels.

## Data basis

Use existing workflow-level scoring data and domain-level result records.

## Acceptance

Issue #298 is complete when the matrix answers where each workflow unlocks hard-domain performance at Level 4.
