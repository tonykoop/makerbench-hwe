# Arbor-style Hypothesis-Tree Runner

Issue [#162](https://github.com/tonykoop/makerbench-hwe/issues/162) asks for an
Arbor / Hypothesis-Tree-Refinement-inspired workflow in MakerBench: a coordinator
creates a research contract, short-lived executor agents test isolated CAD/physics
hypotheses, and evidence rolls up into a structured report. Hardware-engineering
agents need a **scientific-method loop**, not one-shot prompting.

The public, oracle-free runner lives in
[`makerbench/hypothesis_tree.py`](../makerbench/hypothesis_tree.py) and is
unit-tested in `tests/test_hypothesis_tree.py`. It mirrors Arbor (Renmin U. +
Microsoft Research) onto MakerBench discipline and the Workflow Track epic #100.

## Roles

- **Coordinator (PI).** The long-lived `Coordinator` holds the Idea Tree
  (Depth-0 objective → Depth-1 directions → Depth-2+ testable hypotheses), the
  budget ledger, and the promotion gate. It **never edits geometry** — it scores
  evidence and decides pass/fail, so executors stay honest and replaceable.
- **Executor (grad student).** A short-lived, injected callable
  `Executor = (ResearchContract, IdeaNode) -> HypothesisEvidence`. It gets an
  isolated scratch context, tests one hypothesis, and returns structured evidence
  (commands, geometry params, measurements, artifacts, cost, infra error). The
  real CAD/physics rollout (OpenSCAD compile, a StreamForce sim, a worktree run)
  is wired in by the caller; the orchestrator stays pure and deterministic.

## Research contract (intake)

`ResearchContract` is the frozen brief, fixed **before** any branching so every
hypothesis is judged against the same objective and budget:

- `objective`, `task_id`, `fabrication_domain`
- `fixtures` — fixed geometric/material context (bounding box, target mass, …)
- `allowed_tools` — e.g. `["openscad", "trimesh", "streamforce"]`
- `success_metrics` — measurable `SuccessMetric`s (`minimize` / `maximize` / `meet`)
- `token_budget`, `runtime_budget_seconds` — a hard budget the `BudgetLedger`
  tracks; `run_tree` stops before an attempt it cannot afford
- `dev_seeds` (public) + `heldout_required` — dev-and-held-out promotion gate

## Loop

`run_tree(coordinator, executor, heldout_evaluator=..., max_hypotheses=...)`:

1. For each pending hypothesis, while budget remains, dispatch it to the executor.
2. Record + score the returned evidence against the contract.
3. On failure, **backpropagate** an `Insight` to the parent and siblings so the
   search gets smarter with depth instead of repeating the failure.
4. On a dev pass, run the optional held-out gate and promote the first node that
   clears both gates to *current best* (public-dev-seed vs official-held-out-seed
   policy applied to one design change; `makerbench.seed_policy`).

## Isolated scratch runs + evidence persistence

Arbor's executors run in isolated contexts. `scratch_run_executor(inner, ...)`
wraps any executor so each hypothesis runs in its **own fresh scratch directory**
(under a provided root or a tempdir), with the inner executor's working directory
set to that dir. The returned evidence is persisted there as `evidence.json` and
the scratch path is recorded in the evidence's `artifacts`. This realizes the
"short-lived executor in an isolated context" pattern deterministically and
offline — swap the inner executor for a real worktree/OpenSCAD rollout and the
Coordinator/report code is unchanged.

`persist_report(report, out_dir)` writes the full audit trail to a directory:
`report.json` (the serialized `ResearchReport`), `report.md`
(`render_report_markdown`), and `dashboard.json` (`dashboard_summary`).

A runnable end-to-end example is
[`examples/arbor_hypothesis_tree_demo.py`](../examples/arbor_hypothesis_tree_demo.py)
(analytic stub executor) and the scratch-run variant
[`examples/arbor_scratch_run_demo.py`](../examples/arbor_scratch_run_demo.py).

## Output

- `render_report_markdown(report)` — the Arbor-style clean Research Report: the
  contract, the full Idea Tree with per-hypothesis evidence, the backpropagated
  insights, the budget, and the promoted best. Carries the do-not-train canary.
- `dashboard_summary(report)` — a flat dict of leaderboard-comparable scalars
  (counts, budget utilization, best params/measurements). Public metadata only.

## Boundary

- Pure Python over public params; consults no gold answer, held-out fixture, or
  private threshold, so it runs in public CI.
- The held-out evaluator is injected by a maintainer-only caller; the public
  runner never bundles one.
- Off-leaderboard scaffolding (Workflow Track epic #100): no `task_families` or
  `capability_axes` entries, no score churn. Grading stays math/tool-based.
