# Generative-to-Sim: Prompt to Mesh to URDF/MuJoCo

## Intent
Translate a text prompt into a mesh and control contract suitable for sim ingestion.

## Acceptance
- Mesh pipeline is parameterized by prompt constraints.
- URDF/MuJoCo export preserves joint hierarchy and mass assumptions.
- Output includes a validation checklist for topology and sim sanity checks.
