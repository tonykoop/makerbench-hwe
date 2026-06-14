# AI-HWE TwinGrid Sprint — Round R1 (reproducible kit)

Reproducible kit for a **TwinGrid blind A/B** sprint across the AI-HWE workflow-benchmark
ecosystem (makerbench-hwe Epic #100, StudioPipeline/hwe, tonykoop/HWE-Pipeline). Persisted
in-repo so a crash can't lose it and others can reproduce the workflow
(motivated by mangocatalyst's reproducibility issues: makerbench-hwe#43/#44, claude-skills#193).

## Layout
- `persona-map.tsv` — the 9 personas (alice…iris), their lane slug, repo, and target issues.
- `bodies/<persona>.md` — the per-persona assignment body (scope/guardrails/validation/deliverable).
- `pack.md` — the shared Contract Context Pack injected into every handoff.
- `assignment-preamble.txt` — the read-only contract preamble (from the tmux-sprint skill).
- `setup-worktrees.sh` — creates 18 isolated worktrees on persistent disk (`/home/tony/hwe-wt`).
- `gen-handoffs.sh` — emits `handoffs/sprint-<persona>-<A|B>.md` + `persona-launch.generated.tsv`.
- `handoffs/` — generated, ready-to-dispatch contracts (committed for reproducibility).

## Personas (lanes)
| Persona | Lane | Repo | Issues |
|---|---|---|---|
| alice | Contracts-Core (harness_class + RFC) | makerbench-hwe | #87,#88 |
| bob | WorkflowManifest + HII + .mbc | makerbench-hwe | #89,#109 |
| cindy | Deliverable packet (GD&T+STL+G-code) | makerbench-hwe | #103 |
| dan | Run navigation (explorer/library) | makerbench-hwe | #104 |
| elsa | Blender MCP driver | StudioPipeline/hwe | #1 |
| frank | Session recorder + video contract | StudioPipeline/hwe + makerbench | #2, #105 |
| gina | evolution-pipeline skill + Alpha engine | HWE-Pipeline | cs#206, mb#112 |
| henry | Docs (reasoning buckets, challenge spec, templates) | makerbench-hwe | #111,#94,#95 |
| iris | makerbench-logger SDK | makerbench-hwe | #92 |

## Reproduce
```bash
bash setup-worktrees.sh     # create 18 worktrees (side A=Opus grid, B=gpt-5.5 grid)
bash gen-handoffs.sh        # (re)generate handoffs + persona-launch manifest
# then launch two tmux grids (sprint=Opus, twingrid-b=codex gpt-5.5) and dispatch each
# handoff per the tmux-sprint skill (cancel copy-mode -> send -l text -> C-m -> verify).
```

## Conventions
- Side A = Claude Opus (session `sprint`); Side B = codex gpt-5.5 (session `twingrid-b`).
- Blind A/B: both sides solve the same lane independently; manager runs Partner Peek and merges the winner.
- PRs use `Refs`, not `Closes`. Commit+push early (this round was relaunched after a crash lost uncommitted work).
- codex (side B) panes can't reach api.github.com — the manager opens their PRs.
