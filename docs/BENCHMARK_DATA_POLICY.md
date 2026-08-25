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

## Host paths must never reach public JSON

Committed public JSON must not name a host-absolute filesystem path. Such a value
discloses the operator's local directory layout and, on Windows and macOS, their
account name. The leak is not hypothetical: it arrived through captured tool
output, where an OpenSCAD parse error names the temporary file it failed on, and
through perception-artifact paths recorded straight from the run directory.

`makerbench/redaction.py` holds the single pattern set for this class of value.
The runner imports it to scrub at capture time and
`scripts/audit_public_artifacts.py` imports it to fail CI — deliberately the same
patterns, so the guard cannot come to recognise less than the redactor rewrites.

Two canonical forms, chosen by what the field is:

| Field kind | Canonical form | Example |
| --- | --- | --- |
| Structured path (`perception_trace[].artifacts[].path`) | Path relative to the run's own output directory | `perceive/iter_1/view_iso.png` |
| Free text (`perception_trace[].warnings[]`, captured stderr) | `<redacted-host-path>` | `Can't parse file '<redacted-host-path>'!` |

A structured path keeps its run-relative tail because that tail is exact,
reproducible provenance — which iteration and viewport an artifact came from —
and costs nothing to publish. Free text gets a flat token instead: prose cannot
be parsed into a safe tail with any confidence, and the temp filenames inside it
are random and carry no diagnostic value.

Documentation placeholders (`C:\Users\<you>\…`, `/home/${USER}/…`,
`%USERNAME%`) are instructions rather than disclosures and are not flagged, so
setup docs can show real invocations without inventing evasions.

### Grandfathered bundles

`PENDING_HOST_PATH_REDACTION` records the bundles that already carry this leak,
mapped to their known offender count. These cannot simply be rewritten:
`makerbench.attestation.normalized_result_payload` hashes the whole result
payload, so editing any value inside an attested bundle invalidates its
attestation — the fix needs a trusted re-attestation run, not a text edit
(see `docs/ATTESTATION_RUNBOOK.md`).

Recording a *count* rather than a bare filename keeps the guard forward-binding
while that work is pending: an existing offender is tolerated, a new one in the
same file still fails. Delete entries as re-attestation lands; when the map
reaches `{}`, remove it.
