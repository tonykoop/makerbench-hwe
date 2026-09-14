# Code-CAD B-rep proof-round comparison

`config/proof-round-brep.example.json` pins the same three instruments, three
subscription-CLI entrants, seeds, and repetitions as the 2026-09-13 OpenSCAD
proof, while changing only the arena backend to `cadquery`. It is an operator
example, not an auto-launch file: only the sprint manager may authorize and run
the real entrant CLIs, and paid API calls remain disabled.

Before launching, the manager confirms makerbench-hwe PRs #758, #759, and #760
have exact-head `verdict:approve`, copies the example to an untracked local
operator file, chooses a dated gitignored `run_dir`, and explicitly changes
`launch_authorized` there. Lanes and automated tests must never make that
change or invoke the listed entrants.

For comparison, keep the OpenSCAD and CadQuery runs in separate directories.
After both finish, compare `objective_scoreline.json` rows keyed by entrant and
verify from each `run_log.json` that instrument, seed, repetition, and entrant
sets match before interpreting a delta. Report per-entrant objective pass rate
and trial count for each backend; then inspect per-instrument trial objectives
to locate changes in renders, watertightness, volume, envelope, minimum wall,
or body count. Do not compare Elo unless both runs also have an equivalent
blind-vote protocol and voter set.

The result is a backend/modality comparison, not proof that two independently
generated candidates are geometrically identical. Kernel-level equivalence is
covered separately by the real-geometry parity test in
`tests/test_cadquery_mesh_parity.py`, where the same solid is built in both
kernels and sent through the same mesh gate.
