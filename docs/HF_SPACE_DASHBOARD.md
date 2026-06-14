# HF Space dual-league dashboard (#98)

An interactive Hugging Face **Docker** Space that takes MakerBench HWE out of the
CLI into a scannable showcase: two leagues of leaderboards plus an Inspect-a-Run
3D view. This doc covers the architecture, the data flow, and — the load-bearing
part — the boundary that keeps golden seeds off the client.

The Space tree lives under [`spaces/hf_dashboard/`](../spaces/hf_dashboard/). It is
staged for upload by [`scripts/build_hf_docker_space.py`](../scripts/build_hf_docker_space.py),
the Docker analogue of the static-mirror builder `build_hf_space.py`.

## What it shows

**Tab A — dual-league leaderboards.** Two boards that never share a row:

| League | Rows headline… | Population rule |
| --- | --- | --- |
| **Autonomous** | the bare model (+ effort, agent) | `harness_class` ∈ {autonomous, unclassified, ∅} |
| **Workflows** | the *stack* (e.g. `Claude · Blender-MCP`) | any other `harness_class` |

The split is the `harness_class` discriminator from
[`WORKFLOW_TRACK.md`](WORKFLOW_TRACK.md) — orthogonal to `track`
(blind/perception), `result_provenance`, and `verification_status`. Each row
aggregates its runs: mean/best score, tasks and domains covered, the most
autonomous HII observed, and a verified/unverified/pending tally.

**Tab B — Inspect-a-Run.** For any single run: an interactive 3D viewer of the
**submitted** STEP/STL/glB artifact, the WorkflowManifest/HII trace (wall-clock +
tool-call log), the deliverable packet and signed `.mbc` certificate, and the
grader's level-by-level verdict. Every panel is an additive *slot* that degrades
to a "pending"/"none" note when its data is absent — the same honesty contract as
the run-nav explorer it builds on.

## Architecture

```
                build time (scripts/build_hf_docker_space.py)
 run dirs ──▶ generate_run_library ──▶ runs-manifest.json ──▶ dashboard_data ──▶ data/*.json
 (results/   (makerbench-runs-          (#104 contract)        (this PR)         dual_league
  + workflow   manifest-v1)                                                      inspect_index
  run dirs)                                                                      run_details
                                                                                     │
                run time (Docker container, port 7860)                               ▼
 browser ◀── static/ (3-tab UI, three.js viewer) ◀── app.py (FastAPI) ◀── data/*.json + runs/
                                              │
                          STEP B-Rep ─▶ geometry_convert.step_to_glb (pythonocc) ─▶ glTF
```

- **`dashboard_data.py`** — stdlib-only. `build_dual_league`, `build_inspect_index`,
  `build_run_detail`. The single source of truth for the payload shapes; fully
  unit-tested without FastAPI or a browser.
- **`app.py`** — thin FastAPI shell. Serves the static UI, the baked payloads
  (`/api/leagues`, `/api/inspect`, `/api/run/{id}`), and submitted artifacts
  (`/artifacts/{id}/{name}`, path-escape guarded). STEP artifacts are tessellated
  to glTF on the way out.
- **`geometry_convert.py`** — isolates the only heavy dependency (`pythonocc-core`,
  conda-only, installed in the Dockerfile). Returns `False` when absent so CI and
  the data layer never need it; the running Space then offers a STEP download
  instead of an inline render.
- **`static/`** — `index.html` + `app.js` (two tabs, vanilla) + `viewer.js`
  (three.js STL/GLB orbit viewer, pinned to the same version as the Pages site).

## Data flow for a new contribution

Mirrors the existing "verify, don't trust" submission contract
([`SUBMISSION_CONTRACT.md`](SUBMISSION_CONTRACT.md),
[`COMMUNITY_SUBMISSION.md`](COMMUNITY_SUBMISSION.md)):

1. A contributor uploads a manifest + artifact (workflow runs add a
   WorkflowManifest + optional `.mbc`).
2. A **regrade worker** pulls the hidden seeds via **HF repo secrets**, runs the
   *public* grader headless, and re-derives the score and levels.
3. The verified row is appended to a public **Dataset** (JSONL).
4. The build step folds that Dataset (plus committed `results/`) into the
   `runs-manifest.json` the dashboard consumes.

> **Scope of this PR.** Steps 1, 3, and 4 are the public path and are what this PR
> builds (the manifest is already produced by #104; this PR bakes the payloads and
> serves them). **Step 2 — the secret-gated regrade worker — is intentionally not
> implemented here.** It runs hidden seeds and therefore must never live in the
> client-facing container. `build_hf_docker_space.py` stages public artifacts
> only and prints that boundary explicitly.

## Golden-seed safety (the acceptance criterion)

> *"golden seeds never exposed client-side."*

Three layers enforce it:

1. **Source.** Every payload is assembled from public run-dir files only
   (`run.json`, `workflow_manifest.json`, the packet listing). The data layer
   never reads `private/oracles/` and there is no code path from the Space to a
   hidden seed.
2. **Shape.** `build_run_detail` emits a fixed **whitelist** of keys — it does not
   pass `run.json` through verbatim — so a future field added upstream cannot leak
   by accident. A unit test asserts the key set and scans every payload for
   `oracle` / `gold` / `secret` / `held-out` markers.
3. **Image.** The Dockerfile copies only `data/` (baked public payloads), `runs/`
   (submitted artifacts), and the app. Hidden seeds are never baked in; the
   regrade worker that touches them is a separate secret-gated job.

## Build & upload

```bash
python scripts/build_hf_docker_space.py --runs-root <dir-of-run-dirs>
cd dist/hf_docker_space
docker build -t mb-dashboard . && docker run --rm -p 7860:7860 mb-dashboard
# http://localhost:7860 ; then upload:
hf upload <user-or-org>/<space-name> . . --repo-type space
```

Uploading is a manual maintainer action (Hugging Face login required), like the
static mirror in [`PUBLICATION_PLAN.md`](PUBLICATION_PLAN.md). The canonical board
stays the GitHub Pages site; this Space is the interactive companion.
