## Shared Contract Context Pack (read first — identical for all personas)
You are one side of a **TwinGrid blind A/B**: a partner agent is solving this SAME lane independently (you on one grid, them on the other). Do not coordinate. Produce the best artifact you can; the manager compares both at Partner Peek and merges the winner.

**The dogfood loop this serves:** an agentic CAD stack (LLM → Blender MCP → STEP/STL → CNC G-code + GD&T PDF) records its session, emits a WorkflowManifest + signed `.mbc` certificate, exports a fabricable packet, and submits to the MakerBench-HWE *workflow track* — proving the stack on geometry, not hype.

**Who owns which contract (code against the issue body; do NOT block on a partner lane's branch):**
- alice owns `harness_class`/`harness_subclass` + `docs/WORKFLOW_TRACK.md` (mb#87/#88) — NOTE PR #102 already drafts the RFC; reconcile with it.
- bob owns `WorkflowManifest` + Human Intervention Index + `.mbc` certificate format (mb#89/#109)
- cindy owns the DesignDossier deliverable packet — GD&T PDF + STL + G-code + BOM + `packet_manifest.json` (mb#103)
- dan owns the run-navigation generators — per-run `explorer.html` + cross-run `library.html` + `runs-manifest.json` (mb#104)
- iris owns the `makerbench-logger` SDK that emits a WorkflowManifest (mb#92)
If your lane consumes one of these, read that issue body for the schema and **stub the import** — alignment happens at merge.

**Rules (all personas):**
1. **qmd Step-0:** before authoring, run `qmd query "<topic>"` and `qmd search -c makerbench` (collection may be absent — fall back to `qmd query`). Summarize findings in `skill_findings.md`.
2. **Plan first:** post a short plan (files/tests/PR scope) and WAIT for manager approval before editing.
3. **Commit + push EARLY and OFTEN** to your branch (this sprint already lost a round to a crash with uncommitted work). Push after your first real change.
4. **Worktree isolation:** work ONLY inside your assigned worktree.
5. **PRs use `Refs #NNN`, not `Closes`.**
6. **Partner Peek outputs** in your worktree root: the artifact(s), `agent_record.json`, `ready_for_peek.json` (`{"persona","side","status":"ready","branch","primary_artifacts":[...]}`), `skill_findings.md`.
7. Keep diffs tight; the manager verifies your diff touches only intended files before merge.
