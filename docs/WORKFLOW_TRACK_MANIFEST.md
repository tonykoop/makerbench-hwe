# Workflow-track manifest, Human Intervention Index & `.mbc` certificate

> Status: contract v0.1 (additive). Implements mb#89 (WorkflowManifest + HII) and
> mb#109 (`.mbc` certificate). The harness-class / `WORKFLOW_TRACK.md` overview is
> alice's lane (mb#87/#88) — this document covers only the provenance schema and
> the signed certificate, and reconciles with that overview at merge.

## Why a disclosed manifest

The workflow track grades an *artifact* — a STEP/STL produced by an agentic CAD
stack (LLM → Blender MCP → STEP/STL → CNC G-code + GD&T PDF) — with the same hard
L1–L4 geometry scoring as every other row. What it **cannot** do is re-run a human
steering a closed plugin. So a workflow row carries a **disclosed audit log** of
how it was made: the stack, the run's cost/effort metrics, and how much a human
intervened. The artifact is hard-graded; the process is disclosed, never a score
input.

This builds on the existing [`DesignDossier`](../makerbench/schema.py) and
`perception_trace` contracts by composition — a manifest *references* the dossier
it was produced alongside, it does not replace it.

## `WorkflowManifest` (`makerbench/schema.py`)

| Field | Type | Meaning |
|-------|------|---------|
| `schema_version` | str | `"0.1"`. |
| `task_id`, `seed` | str, int | The task instance this run solved. |
| `stack` | `StackDescriptor` | The agentic stack (below). |
| `metrics` | `WorkflowMetrics` | Cost/effort accounting (below). |
| `hii` | `HumanInterventionIndex` | Steering disclosure (below). |
| `provenance_trace` | `ProvenanceTrace` | `tool_call_log_url`, `session_recording_hash`. |
| `dossier` | `DesignDossier?` | The dossier produced alongside, when attached. |

### `StackDescriptor`

Four disclosed slots, each an optional `ComponentVersion` (`name` + optional
`version`) so a row records exactly which build was in the loop — stack drift is as
score-relevant for an agentic run as OpenSCAD version is for the grader:

- `orchestrator` — the driving agent/LLM loop (e.g. `claude-code`, `codex`).
- `framework` — the agent framework/SDK (e.g. `makerbench-logger`, `langgraph`).
- `host_application` — the CAD host being driven (e.g. `blender`, `freecad`).
- `execution_bridge` — the bridge the host is driven through (e.g. `blender-mcp`).

### `WorkflowMetrics`

`wall_clock_seconds`, `inference_tokens`, `tool_calls_count`, `estimated_cost_usd`
— all optional. A run discloses what it can measure and leaves the rest `None`
(never coerced to zero), matching `UsageReport`'s honesty rule.
`estimated_cost_usd` is a disclosed what-if estimate, never presented as an
authoritative bill.

### Human Intervention Index (HII)

Cumulative-effort tiers, **not** a score — a row is more impressive at L0 than L2,
but all three are valid disclosed rows:

| Tier | Meaning |
|------|---------|
| **L0** | Fully autonomous: no human steering between brief and final artifact. |
| **L1** | Light NL steering: human nudged in natural language between iterations. |
| **L2** | Heavy copilot / manual geometry edits: human edited geometry by hand. |

`HumanInterventionIndex` carries an event count per tier plus a derived
`autonomy_ratio` (0..1, where 1.0 = fully autonomous) and `highest_level`. Build it
with the classmethod so the ratio is derived consistently rather than hand-set:

```python
from makerbench.schema import HumanInterventionIndex

hii = HumanInterventionIndex.from_events(l0=11, l1=3, l2=1)
hii.autonomy_ratio  # 0.833333  — L1 costs half autonomy, L2 costs all of it
hii.highest_level   # "L2"
```

`autonomy_ratio = (l0·1.0 + l1·0.5 + l2·0.0) / (l0 + l1 + l2)`; a run with no
recorded iterations is treated as fully autonomous (1.0).

## `.mbc` MakerBench Certificate (`makerbench/certificate.py`)

A workflow run is graded headlessly on the contributor's machine. Before a public
surface (leaderboard, HII badge, Delta-Dossier history) trusts that score, the
bytes must be tamper-evident. The `.mbc` certificate is that substrate: the local
grader bundles the verdict and signs it.

**Payload (`MbcPayload`):** grade `score` + `passed` + per-check `checks`,
`artifact_fingerprint` (+ `artifact_hash_version`), `video_evidence_sha256`,
`autonomy_ratio` + `hii_highest_level`, `toolchain_versions`, `timestamp`, and a
server-issued `nonce` the signature is bound to.

**Envelope (`MbcCertificate`):** `{ payload, signature_alg, signature }`, written
as pretty JSON to a `.mbc` file.

**Signing (v0.1):** `HMAC-SHA256` over the canonical payload bytes (sorted keys,
tight separators, ASCII — identical to `attestation.normalized_result_payload`, so
digests are stable across machines). It proves the payload came from a holder of
the shared key and has not been mutated. The envelope is swappable for a detached
asymmetric signature later without changing consumers.

```python
from makerbench.certificate import MbcPayload, write_mbc, verify_mbc

payload = MbcPayload(
    task_id="vented_plate", seed=0, score=3, passed=True,
    artifact_fingerprint=grade.artifact_sha256,
    autonomy_ratio=hii.autonomy_ratio, hii_highest_level=hii.highest_level,
    toolchain_versions=grader_environment(),
    timestamp="2026-06-13T12:00:00Z", nonce=server_nonce,
)
write_mbc(payload, KEY, path="out.mbc")
verify_mbc("out.mbc", KEY, expected_nonce=server_nonce)  # True
```

`verify_mbc` returns `True` only if the certificate parses, the signature
recomputes exactly (constant-time compare), and — when `expected_nonce` is given —
the nonce matches. **Mutating any byte of the payload** (e.g. raising `score`)
changes the canonical bytes and fails the HMAC check. Malformed input returns
`False` rather than raising. It accepts a path, JSON text, bytes, a dict, or an
`MbcCertificate`.

## Cross-repo JSON Schema

Other repos in the dogfood loop (the `makerbench-logger` SDK, the local grader, the
HF Space verifier) validate against committed JSON Schema rather than importing
makerbench:

- `schemas/workflow_manifest.schema.json`
- `schemas/mbc_certificate.schema.json`
- `schemas/examples/workflow_manifest.example.json`

Regenerate after changing the models (CI can guard staleness with `--check`):

```bash
PYTHONPATH=. python scripts/export_workflow_schemas.py          # write
PYTHONPATH=. python scripts/export_workflow_schemas.py --check  # verify non-stale
```
