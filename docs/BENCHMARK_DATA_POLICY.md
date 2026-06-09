# Benchmark Data Publication Policy

MakerBench public repositories must not publish benchmark answer material,
held-out oracle geometry, or submitted source/vector CAD artifacts that could
train a model to game the benchmark. For community submissions, the public PR is
metadata-only; source artifacts move only through the private
`makerbench-submissions` archive.

Allowed in public repositories:

- Task prompts, schemas, grading harness code, and non-secret validation logic.
- Scalar result JSON, leaderboard data, and aggregate measurements.
- Generated site assets that are needed to present scores, provided they do not disclose oracle geometry or submitted source/vector CAD.
- The `private/oracles` and `private/submissions` git submodule pointers only (the gitlink commit SHA). The submodule **contents** must remain private.

Not allowed in public PRs or the merged default branch:

- Oracle or golden-answer files.
- Submitted CAD/vector source artifacts under `results/**/artifacts/`, including `.scad`, `.dxf`, `.svg`, `.step`, `.stp`, `.brep`, `.obj`, `.ply`, `.off`, `.stl`, and `.3mf`.
- Any derived artifact whose geometry is detailed enough to reconstruct held-out answers or train directly against the benchmark tasks.

## Submitted artifacts: private intake

The artifact audit (`scripts/audit_public_artifacts.py`) blocks the files above
from public history. Result-submission PRs must not force-add or temporarily
stage `results/**/artifacts/*`.

- Contributors open metadata-only public PRs containing result JSON and site data.
- Source artifacts are sent privately to a maintainer and placed in
  `makerbench-submissions/incoming/hwe-pr-<PR>/results/<model>/artifacts/`.
- The maintainer-run `verify-private-submission` workflow regrades from the
  private archive and posts a trusted attestation comment. Public CI verifies the
  attestation against the metadata-only result payload; it never reads private
  artifacts.

Before making a repository public, run:

```bash
python scripts/audit_public_artifacts.py
```

The same audit runs in CI. If it fails, remove the flagged files from the public tree and keep them in private storage.
