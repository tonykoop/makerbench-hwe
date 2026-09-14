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
| **⚖️ Compare Runs** | Two runs side by side, read-only, reusing the same summary/leaderboard endpoints every other tab already calls. |
| **🌙 Nightly Queue** | Read-only cockpit over a `nightly-cad-queue.json`: lease status, per-job status/orphan detection, reconstructed budget spend. |
| **🌅 Morning Review** | Votes on a nightly morning bundle through the same anonymous vote stage as Blind Voting, instead of the standalone `morning-vote/pair-NNN.html` pages. |
| **🩺 Preflight** | Redacted, read-only `nightly_preflight` doctor check — secret values never leave the server, only PRESENT/MISSING/PLACEHOLDER classifications. |
| **Task Matrix** | Every registered instrument task, with reference-image gatekeeper status and family filters. |

A small **Judge & Objective Panel** appears under Blind Voting and Morning Review
after you cast a vote: the objective mesh-gate result for each side, and — if a VLM
judge has already scored that pair out-of-band via `arena judge` — its verdict.
Never shown before a vote; never triggers a judge CLI call itself.

## Windows/RDP launch

From a Windows desktop (including over RDP), `scripts\windows\start-arena-studio.ps1`
starts Arena Studio inside WSL and opens it in the default browser, without needing a
terminal open on both sides of the WSL boundary:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\windows\start-arena-studio.ps1
```

It is self-locating the same way `run-nightly-cad-arena.ps1` is (derives the repo root
from its own location via `$PSScriptRoot` + `wslpath` unless `-RepoWsl` is passed
explicitly), waits for `/api/health` to answer before opening the browser tab, and is
**loopback-only by construction** — there is no `-Host` parameter and it never passes
`--allow-remote`, so Arena Studio started this way is unreachable from any other
machine on the network no matter how it's invoked. Pass `-Port <n>` to use a port other
than 8080, or `-NoBrowser` to start the server without opening a tab.

> **Unverified on a live Windows desktop.** This script was written and covered by
> path-lint/contract tests in a Linux sandbox with no `wsl.exe`-driven Windows browser
> to actually launch against. See the tracking story for someone with real Windows/RDP
> access to confirm it end-to-end.

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
`votes.*.jsonl` or the queue. **Undo** appends a retraction record to both
`votes.blind.jsonl` and `votes.revealed.jsonl` (the original vote lines are never
rewritten or removed), makes the pair votable again, and is honored by the Elo/agreement
consumer (`votes_to_elo_votes()` replays retractions by `(pair_id, voter_id)` before
counting anything, so an undone vote does not stay counted, and a subsequent revote lands
in the correct chronological order); the on-screen undo toast is a client-side ~8s
convenience window, not a server-enforced deadline — the pair stays undo-able past that
window too, just without the toast prompt.

## Zero-WebGL fallback

Interactive 3D orbit is **strictly progressive enhancement**. This matters because a
Windows RDP session can lose its WebGL context mid-session
(`CONTEXT_LOST_WEBGL`) — Studio must never present a blank canvas when that happens:

- On page load, a throwaway `canvas.getContext('webgl2')` probe decides whether the
  WebGL toggle is enabled at all. If WebGL2 is unavailable, the toggle stays disabled
  and the frame turntable is the only option.
- A light-DOM `'error'` listener on each `<model-viewer>` element itself routes through
  the same fallback path as startup detection: force the mode back to the frame
  turntable and disable the WebGL toggle. (A `webglcontextlost` event fired on
  `<model-viewer>`'s internal shadow-DOM canvas is not `composed`, so per the DOM spec it
  never crosses the shadow boundary to a listener on the element or on `window` —
  `<model-viewer>` itself re-dispatches context loss as its own light-DOM `'error'`
  event, which is what Studio actually listens for.)
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
  working as intended, not a bug. Check the on-page notice text for a WebGL-context-loss
  message if it happened mid-session (triggered by `<model-viewer>`'s own `'error'`
  event, not a raw `webglcontextlost` listener — see Zero-WebGL fallback above).
- **"Cross-origin POST refused" (403)**: something is POSTing to Studio's API from a
  different origin than the page itself (e.g. a proxy that rewrites the `Origin`
  header). Serve Studio directly rather than through an origin-rewriting proxy.
