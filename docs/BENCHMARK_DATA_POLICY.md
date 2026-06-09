# Benchmark Data Publication Policy

MakerBench public repositories must not **retain in durable history** (the merged default branch) benchmark answer material, held-out oracle geometry, or submitted source/vector CAD artifacts that could train a model to game the benchmark.

Allowed in public repositories:

- Task prompts, schemas, grading harness code, and non-secret validation logic.
- Scalar result JSON, leaderboard data, and aggregate measurements.
- Generated site assets that are needed to present scores, provided they do not disclose oracle geometry or submitted source/vector CAD.
- The `private/oracles` and `private/submissions` git submodule pointers only (the gitlink commit SHA). The submodule **contents** must remain private.

Not allowed in the merged default branch:

- Oracle or golden-answer files.
- Submitted CAD/vector source artifacts under `results/**/artifacts/`, including `.scad`, `.dxf`, `.svg`, `.step`, `.stp`, `.brep`, `.obj`, `.ply`, `.off`, `.stl`, and `.3mf`.
- Any derived artifact whose geometry is detailed enough to reconstruct held-out answers or train directly against the benchmark tasks.

## Submitted artifacts: staging vs. durable history

The artifact audit (`scripts/audit_public_artifacts.py`) blocks the files above from the **merged default branch**, which is the durable, indexed public surface. The guarantee this policy makes is **durable-history containment**, not zero public exposure:

- During a result-submission PR, the contributor stages source artifacts under `results/**/artifacts/` so public regrade can re-grade them. A maintainer runs the `archive-submission` workflow to push them into the private `makerbench-submissions` archive, then the contributor removes them; squash-merge keeps the merged branch clean (see `CONTRIBUTING.md`).
- While staged, those artifacts **are** visible in the PR diff and the (possibly forked) PR branch history until removed. They are short-lived and never enter `main`, but this is **not** zero public exposure. A contributor who needs stronger secrecy should coordinate an out-of-band private submission with a maintainer rather than staging artifacts in a public PR.

Before making a repository public, run:

```bash
python scripts/audit_public_artifacts.py
```

The same audit runs in CI. If it fails, remove the flagged files from the public tree and keep them in private storage.
