## dan — Run-Nav (makerbench-hwe · mb#104)
### Why
Submissions scale to hundreds of runs; reuse the proven instrument-library navigation.
### Scope
1. `scripts/generate_run_explorer.py` — per-run explorer.html (artifact viewer slot, packet links, grader verdict, WorkflowManifest/HII trace, video slot). ADAPT `/mnt/c/Users/Tony/Documents/GitHub/_meta/wolfram-cloud-sync/generate_explorer.py` (inject additively — do not destroy galleries).
2. `scripts/generate_run_library.py` — cross-run library.html filterable by harness_class/domain/HII/verification/score + search. ADAPT `/mnt/c/Users/Tony/Documents/GitHub/instruments/_meta/instrument-showcase/scripts/generate_library.py`.
3. Emit `runs-manifest.json`.
### Guardrails
Additive scripts + fixtures; no grader changes.
### Validation
Run both against 2 fixture run dirs → valid HTML + manifest w/ 2 entries.
### Deliverable
PR `feat(workflow-track): run explorer + library generators` — `Refs #104`.
