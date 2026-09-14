# Arena Studio — web cockpit for the Code-CAD A/B Arena

Arena Studio (Epic #694) is a local FastAPI web interface for running, monitoring,
voting on, and analyzing the Code-CAD A/B Arena (Epic #421), replacing the earlier
fragmented CLI scripts and terminal log grepping with a single browser tab.

## Launch

```
python3 -m makerbench.cli arena studio --run-dir <path-to-a-run> --port 8080
```

```
 Usage: python -m makerbench.cli arena studio [OPTIONS]

 Launch the MakerBench Arena Studio web interface (Issue #696).

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --run-dir             TEXT     Initial run directory to load in Arena        │
│                                Studio.                                       │
│ --host                TEXT     Bind host. [default: 127.0.0.1]               │
│ --allow-remote                 Allow binding Arena Studio to a non-loopback  │
│                                interface.                                    │
│ --port                INTEGER  Bind port. [default: 8080]                    │
│ --registry            TEXT     Arena registry JSON path.                     │
│                                [default: tasks/code_cad_arena/registry.json] │
│ --help                         Show this message and exit.                   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

`--run-dir` is optional — Studio auto-discovers other runs and lists them in the
**Active Run** dropdown in the header regardless. Once running, open the URL it prints
(`http://127.0.0.1:8080/` by default).

## The tabs

| Tab | What it's for |
| --- | --- |
| **Overview** | Run-level stats (votes cast, model/instrument counts, trial count) and the run's raw config JSON. |
| **🚀 New Competition** | The task matrix launcher: pick instruments and models, then launch a competition run. |
| **Blind Voting** | The A/B voting stage — see [Voting flow](#voting-flow) below. |
| **Leaderboard & Agreement** | Elo standings per model plus the subjective-vs-objective agreement scatter plot (Spearman ρ). |
| **Task Matrix** | Every registered instrument task, with reference-image gatekeeper status and family filters. |

## Voting flow

Each pair presents two anonymized candidates ("Candidate A" / "Candidate B") side by
side with a defect checklist and a vote bar underneath. See
[Anonymity model](#anonymity-model) for what "anonymized" means here.

**Viewer modes**, top-right of the vote stage:

- **🔄 24-Frame Turntable (Zero-WebGL)** — the primary, default view. A pre-rendered
  24-frame azimuth sequence swapped via plain `<img>` tags: drag to scrub, auto-rotate
  when idle, left/right arrow keys to scrub when a turntable panel has keyboard focus.
  Frames preload in the background with a visible `Loading frames… N/24` indicator.
- **🧊 Interactive 3D (WebGL)** — a `<model-viewer>` orbit view, offered only as
  progressive enhancement. The toggle button starts disabled and is only enabled after a
  real `WebGL2` feature-detection pass on page load. See
  [Zero-WebGL fallback](#zero-webgl-fallback).

**Keyboard shortcuts** (shown live in the UI, above the vote stage):

| Key | Action |
| --- | --- |
| `A` / `L` / `←` | Candidate A is better |
| `T` / `D` / `↓` | Draw / equal |
| `B` / `R` / `→` | Candidate B is better |
| `S` | Skip — show another pair without casting a vote |
| `U` | Undo the last vote |
| `1` `2` `3` | Toggle Candidate A's defect checklist (missing parts / misaligned / distorted proportions) |
| `Shift+1` `Shift+2` `Shift+3` | Same, for Candidate B |

`←`/`→` are context-sensitive: pressed while a turntable panel has keyboard focus
(click it, or `Tab` to it) they scrub that panel's frames instead of casting a vote.

**Skip** advances a read-only cursor into the still-unvoted pairs — it never mutates
`votes.*.jsonl` or the queue. **Undo** appends a retraction record to
`votes.blind.jsonl` (the original vote line is never rewritten or removed) and makes the
pair votable again; the on-screen undo toast is a client-side ~8s convenience window, not
a server-enforced deadline — the pair stays undo-able past that window too, just without
the toast prompt.

## Zero-WebGL fallback

Interactive 3D orbit is **strictly progressive enhancement**. This matters because a
Windows RDP session can lose its WebGL context mid-session
(`CONTEXT_LOST_WEBGL`) — Studio must never present a blank canvas when that happens:

- On page load, a throwaway `canvas.getContext('webgl2')` probe decides whether the
  WebGL toggle is enabled at all. If WebGL2 is unavailable, the toggle stays disabled
  and the frame turntable is the only option.
- A capture-phase `window.addEventListener('webglcontextlost', …)` listener — which also
  catches context loss from the `<model-viewer>` element's own internal (shadow-DOM)
  canvas — and continued `isContextLost()` checks both route through the same fallback
  path: force the mode back to the frame turntable and disable the WebGL toggle.
- `<model-viewer>` itself is only ever instantiated in the DOM the first time WebGL mode
  is actually selected (never present as a static element), because its underlying
  three.js renderer probes for a WebGL context — and throws console errors — the instant
  it exists, even unused.

An opt-in Playwright test (`tests/test_arena_studio_zero_webgl.py`, skipped by default —
not a project dependency) drives a real headless Chromium with WebGL genuinely disabled
and asserts the turntable renders a real frame, visibly rotates, and produces zero
console errors. Run it locally with:

```
pip install playwright
playwright install chromium
python3 -m pytest tests/test_arena_studio_zero_webgl.py -v
```

## Anonymity model

Votes are blind: the pre-vote payload never carries an entrant or model identifier.

- Candidate ids in `/api/runs/{run_id}/queue` responses are per-pair opaque handles
  only ("left" / "right" side, keyed by an opaque `pair_id`) — the underlying
  `trial_id` (which embeds the entrant/model name) is never sent to the client before a
  vote is cast.
- Every asset URL served to the vote stage (preview image, GLB, turntable frames) is a
  content-addressed alias under `vote_pages/blind/<pair_id>-<side>...` — never the raw
  artifact path, which would embed the entrant name.
- Identity is revealed only after a vote is recorded, in `votes.revealed.jsonl` — never
  in the DOM, a fetch payload, the page title, or a console log before that point.

## Security model

Arena Studio binds to `127.0.0.1` by default and refuses to bind elsewhere without an
explicit opt-in:

- `--host` defaults to `127.0.0.1`. A non-loopback host is refused (exit code 2) unless
  `--allow-remote` is also passed.
- Every `POST` request is checked against a same-origin guard
  (`require_same_origin_for_posts` middleware): a request whose `Origin` header doesn't
  match the request's own host/scheme gets a `403`. There is no wildcard CORS.
- The `/runs/{run_id}/vote_pages/{file_path}` static-file route only ever resolves
  inside that run's own `vote_pages/` directory — run ids are looked up through
  `discover_runs()`, never accepted as raw filesystem paths.

## Troubleshooting

- **Studio won't start**: `uvicorn` is required (`pip install uvicorn`); the CLI exits
  with a clear message if it's missing rather than a stack trace.
- **A vote stage panel stays on a blank frame turntable and the WebGL toggle is
  greyed out**: expected on a machine/session without WebGL2 — this is the fallback
  working as intended, not a bug. Check the browser console for a
  `webglcontextlost`-triggered fallback message if it happened mid-session.
- **"Cross-origin POST refused" (403)**: something is POSTing to Studio's API from a
  different origin than the page itself (e.g. a proxy that rewrites the `Origin`
  header). Serve Studio directly rather than through an origin-rewriting proxy.
