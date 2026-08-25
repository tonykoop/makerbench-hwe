## bob — Manifest-HII + .mbc (makerbench-hwe · mb#89, mb#109)
### Why
The provenance schema every plugin emits and the signed certificate the leaderboard/badges trust.
### Scope
1. Define `WorkflowManifest` in `makerbench/schema.py` extending DesignDossier/perception_trace: `stack` (orchestrator, framework, host_application, execution_bridge), `metrics` (wall_clock, tokens, tool_calls), Human Intervention Index L0/L1/L2 + `autonomy_ratio` float.
2. Define `.mbc` certificate: signed payload (grade verdict, artifact fingerprint, video_evidence hash, HII/ratio, toolchain versions, timestamp) + `write_mbc()`/`verify_mbc()` (HMAC over a nonce ok for v0.1).
3. Export a JSON Schema so other repos validate against it.
### Guardrails
Additive; don't break DesignDossier consumers.
### Validation
Round-trip test: build manifest, write+verify `.mbc`, mutate a byte → verify fails.
### Deliverable
PR `feat(workflow-track): WorkflowManifest + HII + .mbc` — `Refs #89 Refs #109`.
