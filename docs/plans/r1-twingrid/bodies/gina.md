## gina — Evolution-Skill + Alpha engine (HWE-Pipeline · realizes claude-skills#206, mb#112)
### Why
The Evolution Pipeline skill (prototype→finished-good PLM/DFM). Must become `maker:evolution-pipeline`. Build the Alpha engine now.
### Scope
1. Scaffold the skill MIRRORING `makerspace` (read `/home/tony/.claude/plugins/cache/tony-koop/maker/1.1.1/skills/makerspace/`): `SKILL.md` (frontmatter name: evolution-pipeline, version 0.1.0, last-updated, description: >-, homepage HWE-Pipeline), `manifest.yaml`, `references/` (dfm-checklist, manufacturing-process-selection, supplier-validation, cost-volume-analysis), `agents/specialists/` (dfm-reviewer, manufacturing-planner, cost-analyst), `scripts/`, `examples/`.
2. Build the **Alpha Workspace Compiler** `scripts/alpha_compile.py`: ingest master CAD + a local tool-matrix profile (model on makerspace `assets/templates/shop-equipment-profile.yaml`), downgrade fidelity (reslice to printable shell / flatten to laser-nested panels), emit a fabrication packet. Worked `examples/example-hardware-evolution.md`.
3. Draft engine issues into `ISSUES.md` (Beta Vendor Broker → mb#82/#81; Production BOM/PLM → mb#112) for the manager to file.
### Guardrails
Do NOT edit the marketplace repo/manifest — the MANAGER vendors the winning skill + registers `maker:evolution-pipeline` at merge.
### Validation
`python scripts/alpha_compile.py --help`; run on a tiny fixture STL + sample tool profile → packet dir emitted.
### Deliverable
PR `feat: evolution-pipeline skill v0.1.0 + Alpha compiler` — `Refs` claude-skills#206.
