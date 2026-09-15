## cindy — Deliverable-Packet (makerbench-hwe · mb#103)
### Why
Turns a graded artifact into a fabricable maker packet.
### Scope
1. Extend DesignDossier (schema.py) with optional packet: `drawing_pdf` (GD&T), `mesh_stl`, `cnc_gcode` (+ machine profile/post/tools disclosed), `bom_csv`, `sourcing_csv`, `packet_manifest.json` (roles + per-file sha256).
2. Completeness hooks in `makerbench/dossier_scoring.py` (BOM count vs assembly; G-code bounds enclose part). Disclosure-grade, not a hard gate.
3. `docs/DELIVERABLE_PACKET.md`.
### Guardrails
Optional everywhere; don't require it for existing tasks.
### Validation
Unit: fixture packet scores complete; mismatched BOM flags incomplete.
### Deliverable
PR `feat(workflow-track): deliverable packet (GD&T+STL+G-code)` — `Refs #103`.
