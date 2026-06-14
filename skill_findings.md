# Step-0 findings

- Handoff: `/home/tony/hwe-wt/_kit/docs/plans/r1-twingrid/handoffs/sprint-alice-B.md` assigns alice side B to add workflow-track harness-class contracts for mb#87 and mb#88.
- Direct manager instruction for this run says not to run `qmd`; I used local file reads and `rg` only.
- Branch/worktree: `alice/b-r1-contracts-core` in `/home/tony/hwe-wt/alice-b`, initially clean at `origin/main`.
- `docs/WORKFLOW_TRACK.md` was absent on this branch, so this slice creates it from the issue summary in the handoff and the local `docs/DESIGN.md` style.
- `makerbench/schema.py` already has top-level harness disclosure through `agent_identifier`, `runner_environment`, `hardware_environment`, and `grader_environment`; `harness_class` and `harness_subclass` were new fields.
- `makerbench/cli.py` constructs `RunResults` in the `run` command and is the right place to wire a `--harness-class` flag.
- `site/build_data.py` and `makerbench/viewer_export.py` reproduce model row keys; assisted workflow rows need distinct keys so they do not rank or attach meshes as autonomous rows.
- Guardrail check: no private oracle files were read and no graders were modified.
