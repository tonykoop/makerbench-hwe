# bob (side A) — R1 skill_findings (NO qmd; grounded by rg/read)

**Lane:** WorkflowManifest + Human Intervention Index + `.mbc` certificate (mb#89, mb#109).

## What already exists (read directly)
- `makerbench/schema.py` (636 lines) — pydantic v2 `BaseModel` contracts. Conventions: every
  manifest carries `schema_version="0.1"`, fields use `Field(default=..., description=...)`, types
  are additive/optional so legacy bundles stay valid. `DesignDossier` (line 419) bundles
  artifacts/BOM/process/verification; `PerceptionObservation`/`perception_trace` (line 457) is the
  iteration feedback log. `RunResults.signature` (line 626) is an Optional[str] — no signer wired yet.
- `makerbench/attestation.py` — canonical JSON pattern to mirror: `json.dumps(payload, sort_keys=True,
  separators=(",",":"), ensure_ascii=True).encode()` then `hashlib.sha256(...)`. Marker + fenced JSON
  comment format for PR attestations. No HMAC anywhere yet — `.mbc` introduces the first signed payload.
- `makerbench/provenance.py` — `grader_environment()` returns toolchain versions (openscad, trimesh,
  manifold3d, shapely, numpy, …) — reusable as the `.mbc` `toolchain_versions` source.
- pydantic 2.12.5, python3 imports `makerbench` cleanly from this worktree.

## What does NOT exist yet (so I don't block on it)
- No `WorkflowManifest`, no `workflow_manifest.json`, no `.mbc`, no `HII`/`autonomy_ratio` anywhere.
- No `docs/WORKFLOW_TRACK.md` — that is **alice's** lane (mb#87/#88, drafted in PR #102), unmerged.
  I name my doc `docs/WORKFLOW_TRACK_MANIFEST.md` to avoid a collision; reconcile at merge.
- No `schemas/` dir and no JSON-schema export anywhere — I create `schemas/` at repo root.
- No HMAC/nonce signing primitive — `.mbc` adds `write_mbc()`/`verify_mbc()` (HMAC-SHA256 over a nonce, v0.1).

## Plan executed
Additive only. `schema.py` gains `StackDescriptor`, `WorkflowMetrics`, `HumanInterventionIndex`,
`ProvenanceTrace`, `WorkflowManifest`. New `makerbench/certificate.py` for the `.mbc` payload +
write/verify. `schemas/*.schema.json` exported for cross-repo validation + an example manifest.
`tests/test_workflow_manifest.py` round-trips manifest + `.mbc` and asserts a 1-byte mutation fails verify.
