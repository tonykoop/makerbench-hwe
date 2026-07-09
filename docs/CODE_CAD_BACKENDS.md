# Code-CAD Arena compile backends (#627)

The Code-CAD Arena compiles each entrant's CAD source to a mesh + preview, then
scores that mesh through the oracle-free objective gate. Historically the
compile layer was **OpenSCAD-only**. This adds a small backend registry so the
same arena can also score entrants authored for kernels that only run on
Windows — **Fusion 360** and **SolidWorks** — without changing the Elo,
objective, or vote paths.

## The axis

| backend       | kernel runs on | how it compiles                                             |
| ------------- | -------------- | ---------------------------------------------------------- |
| `openscad`    | WSL/Linux      | shells out to the `openscad` binary (unchanged default)    |
| `fusion`      | Windows        | job-dir handshake → Fusion 360 API exports STL + PNG       |
| `solidworks`  | Windows        | job-dir handshake → SolidWorks COM exports STL + PNG       |

Everything is behind one contract:

```python
Compiler = Callable[[Path, Path], RenderArtifacts]   # (source_path, out_dir) -> artifacts
```

`makerbench/code_cad_backends.py` exposes:

- `BACKEND_COMPILERS: dict[str, Compiler]` and `compiler_for_backend(name, *, jobs_root=..., timeout_s=...)`
- `make_job_dir_compiler(jobs_root, backend, *, poll_interval_s, timeout_s)` — the WSL↔Windows handshake
- `backend_preflight(backend, jobs_root) -> (ok, detail)` — the analogue of `render.openscad_available()`
- `gate_factory_for_backend(backend)` — gate override (see *Assembly part-counter* below)

## Why a job directory (not RPC/COM)

The arena loop runs on the WSL/Linux side. SolidWorks and Fusion are driven
from Windows via COM / the Fusion API and cannot be called from Linux. Rather
than stand up a socket server, the two sides rendezvous through a shared
**job directory** on disk (`\\wsl$` or `/mnt/c` both work): the Linux side drops
a job and polls a status file; a Windows-side watcher services it. No ports, no
daemon protocol, trivially resumable, and the whole exchange is inspectable as
plain files.

## On-disk contract

For each trial the Python compiler writes, under `<jobs_root>/<trial_id>/`:

```
jobs/<trial_id>/
  input/<source name>      # the entrant source, copied verbatim
  status.json              # { "state": "pending", "backend": ..., "source": ..., "input": ..., "artifacts": {...} }
  artifacts/               # (created; the watcher fills it)
    output.stl             #   <- watcher writes: exported mesh (STL)
    preview.png            #   <- watcher writes: preview image (optional)
```

`status.json` is the rendezvous. States:

| state       | written by | meaning                                                        |
| ----------- | ---------- | -------------------------------------------------------------- |
| `pending`   | Python     | job staged, awaiting a watcher                                 |
| `done`      | watcher    | STL (+ optional PNG) exported under `artifacts/`               |
| `error`     | watcher    | compile failed; `error` field carries the message             |
| `timeout`   | Python     | budget expired before a watcher finished (terminal, honest 0.0)|

Both sides write `status.json` **atomically** (temp file + rename) so a poll
never reads a half-written file. `trial_id` is taken from the run's
`render/<trial_id>` out-dir name (falling back to the source file stem).

The Python side polls `status.json` every `poll_interval_s` until `done`/`error`
or until `timeout_s` (the per-entrant compile budget, equal for every entrant —
override with `MAKERBENCH_ARENA_BACKEND_TIMEOUT_S`). On `done` it copies
`artifacts/output.stl` and `artifacts/preview.png` into the trial's `out_dir`
and returns `RenderArtifacts`.

## Failure signaling — honest 0.0, never a crash

The job-dir compiler mirrors the OpenSCAD path exactly: **on any failure it
raises `render.CompileError`.** `evaluate_objective_trial` catches that and
records an auto-fail row with `objective_pass_rate = 0.0` and `render_ok =
false`, so the entrant is scored as an honest zero rather than aborting the run.
Failures that raise:

- watcher flips to `error` (kernel/rebuild failure),
- the budget expires (`timeout`),
- `done` but the STL is missing or empty.

A missing **preview** PNG is *non-fatal* (the mesh gate scores off the STL); the
candidate simply cannot enter blind voting for that cell — the same tolerance
the pre-exported-STL ingest path uses.

## Assembly part-counter decision

The objective gate's assembly fallback (`count_standalone_part_modules`)
re-invokes OpenSCAD to count zero-arg part *modules* — a rescue for correctly
*mated* assemblies that fuse into one connected component (#596). That is
meaningless for a Fusion/SolidWorks source. So for job-dir backends
`gate_factory_for_backend` injects a **null part-counter (always 0)**: the
assembly check for these backends relies solely on the exported mesh's disjoint
connected-component count. A mated assembly modeled as one fused body is *not*
given the standalone-module credit under these backends. A backend-native part
counter (e.g. counting SolidWorks bodies/components at export time and writing
the count into `status.json`) is a documented future extension. OpenSCAD's
behavior is unchanged.

## CLI

```bash
# OpenSCAD (default, unchanged)
makerbench arena run --run-dir runs/code_cad_arena/r1 --instruments ocarina --models m1,m2

# Windows-side kernel via the job-dir handshake
makerbench arena run --run-dir runs/code_cad_arena/r1 \
    --instruments ocarina --models adam-fusion,eve-fusion \
    --backend fusion --jobs-root runs/code_cad_arena/r1/backend_jobs

# same axis on ingest-candidate (only when compiling live source; a --stl skips it)
makerbench arena ingest-candidate --run-dir runs/code_cad_arena/r1 \
    --instrument ocarina --entrant adam-solidworks --scad part.sldprt \
    --backend solidworks
```

`--backend` defaults to `openscad`. `--jobs-root` defaults to
`<run-dir>/backend_jobs`. For a non-OpenSCAD backend the CLI runs
`backend_preflight` (jobs dir writable + watcher-heartbeat report) instead of
the hard `openscad` binary check — it never requires `openscad` to be installed.

## Windows watcher

`scripts/arena_windows_backend_watcher.ps1` is the Windows half — a documented
**reference stub**. It implements the full job-dir contract (scan → claim
pending jobs for its `-Backend` → export → flip `status.json`, plus a
`watcher.heartbeat`), with the actual SolidWorks-COM / Fusion-API export calls
left as clearly marked `TODO` blocks that throw by default (so nothing fakes a
compile). It is never run in CI. Start it Windows-side pointed at the same jobs
dir:

```powershell
./scripts/arena_windows_backend_watcher.ps1 -JobsRoot 'C:\runs\arena\r1\backend_jobs' -Backend solidworks
```
