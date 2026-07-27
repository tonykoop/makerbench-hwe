# `site/build_data.py` Workflows League tab (Issue #299, round2)

## Scope

Separate workflow-environment entries into a dedicated Workflows League while keeping Autonomous Core unchanged for entries without `workflow_env`.

## Build pipeline changes

- Parse `workflow_env` in `site/build_data.py`.
- Route workflow entries to a dedicated Workflows League tab.
- Keep Autonomous Core league unchanged for non-workflow entries.

## UI behavior

- Render Workflows League tab in site output.
- Show recipe tags on each workflow row.
- Include Efficiency Frontier chart within the Workflows League tab.

## Integrity

Tabs are mutually exclusive by schema route:

- workflow entries do not bleed into Autonomous Core,
- non-workflow entries do not appear in Workflows League.

## Acceptance

Issue #299 is complete when the tabbed separation is enforced and frontier/chart rendering is correctly scoped.
