# Workbench API and job runner (#788 W3)

The design workbench lets a person open a candidate (an arena trial, an
instrument master, or a blank), edit its source or its declared parameters,
compile the draft **in the sandbox**, see the preview and objective checks,
save it as an append-only revision, and compare revisions. This document is
the server side: routes under `/api/workbench/`, the detached job process, and
the guards. The screen is W4; parameters tab W5; model revisions W6; export W7.

Modules: `makerbench/arena_studio/workbench.py` (service + job body),
`makerbench/arena_studio/routes_workbench.py` (routes), the store in
`makerbench/workbench_store.py` (`docs/WORKBENCH_STORE.md`), the compilers in
`makerbench/scad_sandbox.py` and `makerbench/cadquery_backend.py`, the
parameter model in `makerbench/cad_params.py` (`docs/WORKBENCH_PARAMETERS.md`).

## Start the Studio with masters available

```
makerbench arena studio --instruments-root /path/to/instruments
```

`--instruments-root` is the same root `arena run` uses: registry `repo_path`
values are relative to it. Without it the workbench still works for trials and
blanks; `GET /sources/masters` and master origins answer 400 with the reason.

## Routes

All under `/api/workbench/`. Ids follow the task-id rule
`[a-z0-9][a-z0-9_-]{0,63}`; anything else is 404. Every POST needs the
Studio's same-origin `Origin`, a trusted `Host`, **and**
`Content-Type: application/json` (415 otherwise).

| Route | Purpose | Errors |
|---|---|---|
| `GET /designs` | designs with title, instrument, backend, origin kind, latest revision, pick | |
| `POST /designs` → 202 | `{"trial": {run_id, trial_id}}` \| `{"master": {instrument_id, file}}` \| `{"blank": {backend, instrument_id}}` plus `title`, `voter`. Copies the source, creates the design and its origin draft, enqueues the compile. Returns `design_id`, `draft_id`, `design`, `draft` | 400 origin, 404 run/trial/master, 429 queue full |
| `GET /designs/{d}` | `design.json`, revisions (last 200), curation state, drafts with live job status | 404 |
| `GET /designs/{d}/revisions/{r}` | provenance, objective payload, artifact names | 404 |
| `GET …/revisions/{r}/source` | `text/plain`, `Cache-Control: no-store` | 404 |
| `GET …/revisions/{r}/parameters` | the W1 parameter model of that revision | 404 |
| `GET …/revisions/{r}/artifacts/{name}` | `preview.png`, `output.stl`, `model.glb`, `output.step` | 404 |
| `GET /designs/{d}/compare?a=&b=` | unified diff rows, parameter delta, objective delta, both provenance payloads | 404 |
| `POST /designs/{d}/drafts` → 202 | exactly one of `source` (edit), `params` (apply `{name: value}` to the parent revision), `revise` (W6: 501). `parent_rev_id` names the parent | 400, 404, 409 no-op apply / missing parent, 413, 429 |
| `GET /designs/{d}/drafts/{j}` | draft with `job` (`queued\|running\|succeeded\|failed\|cancelled\|interrupted`), `queue_position`, objective when done, artifact names | 404 |
| `GET …/drafts/{j}/source` | `text/plain`, no-store | 404 |
| `GET …/drafts/{j}/log/stream?tail=&follow=` | SSE tail of `job.log`; `event: end` with the final status when the job finishes (or immediately with `follow=false` on a finished job) | 404 |
| `GET …/drafts/{j}/artifacts/{name}` | contained draft artifacts | 404 |
| `POST …/drafts/{j}/cancel` | kills the job's process group; the draft becomes `cancelled`; the next queued job starts | 404, 409 already finished |
| `POST /designs/{d}/revisions` → 201 | `{draft_id, note}` promotes a `succeeded` draft to an immutable revision | 404, 409 not saveable / already saved, 413 note |
| `POST /designs/{d}/curation` → 201 | `{rev_id, pick, title, note, voter}` appends to the curation log; returns the row and the effective state | 404 |
| `GET /sources/masters?instrument=` | the instrument's `cad/*.scad` and `cad/*.py` files by name (any case of `cad/`) | 400 no root, 404 |

There is no update or delete route for revisions, by design.

## Jobs

`POST /designs` and `POST /drafts` write the draft (`source.*`, `draft.json`)
and enqueue it. The service launches
`python -m makerbench.cli arena workbench-job --draft-dir <dir> --registry <path>`
detached (own session, stdout+stderr to `job.log`), the same shape as the
Studio's arena launches. The job:

1. writes `params.json` (pure text analysis, W1);
2. compiles with `compiler_for_backend(backend, sandboxed=True)`: OpenSCAD
   inside the W0 Bubblewrap (Xvfb inside for the preview), CadQuery in its own
   bwrap. **No host compile exists in this path.** A sandbox that cannot start
   is a `failed` draft whose error starts with `sandbox_unavailable` (exit 2);
   a candidate defect is a `failed` draft with the compiler's message (exit 1);
3. scores with the instrument's registry gates (`mesh_objective_gate`) when
   the instrument is in the arena registry; otherwise `objective.declared` is
   `false` with the note "gates not declared";
4. writes `objective.json` (with `sandbox: {kind: bwrap, verified, xvfb}`) and
   the final `job` block.

**Limits:** one running job per design, two per server, a queue of eight
(429 beyond that; the rejected draft is never created). **Cancel** signals the
job's process group. **Restart:** on start-up every `running` draft whose pid
is not a live `workbench-job` for that directory becomes `interrupted`, and
`queued` drafts re-enter the queue in creation order.

## Guards (each has a test that goes RED when the guard is removed)

| # | Guard | Where |
|---|---|---|
| G1–G4 | compiles only in the sandbox, fail closed, resource limits | W0 (`scad_sandbox`), `run_job` |
| G6 | same-origin POST, TrustedHost, JSON content type | app middleware + `require_json` |
| G7 | source ≤ 256 KiB, note/title ≤ 2 KiB, feedback ≤ 8 KiB, ≤ 500 params, ≤ 200 revisions per page | route models + store |
| G8 | 1 job per design, 2 per server, queue of 8 → 429 | `WorkbenchService._check_capacity/_pump` |
| G9 | author text is inert | stored verbatim, rendered as text (W4 contract test) |
| G10 | id rule, containment, no symlinks | store (`docs/WORKBENCH_STORE.md`) |
| G11 | append-only revisions | store; no update/delete route |
| G12 | `runs/workbench/` is never a run; blind screens never link the workbench; a full flow never writes under an arena run | `service._discover_run_paths`, tests |
| G13 | masters only via registry `repo_path`, under `cad/`, never `private`, never a symlink; trial sources only inside their run dir | `workbench._master_cad_dir/_master_source/_trial_source` |
| G15 | no host path in any JSON body or SSE line | `PublishedJSONResponse`, `publish` on SSE |
