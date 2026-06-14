## alice — Contracts-Core (makerbench-hwe · mb#87, mb#88)
### Why
Root contracts the whole workflow track integrates against.
### Scope
1. Reconcile/extend `docs/WORKFLOW_TRACK.md` (PR #102 may already start it): harness_class = autonomous|assisted-workflow; harness_subclass = api-driven-code|gui-injected-copilot; league separation (assisted never ranks vs autonomous; caps at artifact-verified); disclose-but-don't-prove trust model. Match `docs/DESIGN.md` style.
2. Add `harness_class` + `harness_subclass` to RunResults in `makerbench/schema.py` (Pydantic BaseModel — Optional, default `autonomous`, backward-compatible).
3. Wire a `--harness-class` flag through `makerbench/cli.py`.
### Guardrails
Additive only; do NOT break autonomous scoring or result loading; do NOT modify graders.
### Validation
`python -c "from makerbench.schema import RunResults"` ; repo test entrypoint for schema/result.
### Deliverable
PR `feat(workflow-track): harness_class + WORKFLOW_TRACK.md` — `Refs #87 Refs #88`.
