# Arena Studio

Arena Studio is MakerBench's local web cockpit for the Code-CAD A/B arena:
run discovery, dry-run launches with live logs, blind voting, agreement
analytics, DoE queue building, the nightly cockpit and morning review.

> This page starts with launching the Studio API. The full user guide for the
> rebuilt Studio UI starts at [Finding your way around](#finding-your-way-around).

Arena Studio is MakerBench's local web cockpit for the Code-CAD A/B arena
(Epic #421, Studio epic #694). It covers the whole evening in one browser tab:
find a run, launch a dry run and watch its log, vote blind, read agreement
analytics, compare runs, plan a DoE queue, check the nightly queue, and review
the morning bundle.

The UI is a small Preact + htm app vendored into the package (no build step,
no CDN, nothing fetched from the network), served by the same FastAPI server
as the Studio API.

## Launch (WSL / Linux)

```
python3 -m makerbench.cli arena studio --port 8080
```

Then open `http://127.0.0.1:8080/`.

```
 Usage: python -m makerbench.cli arena studio [OPTIONS]

 Launch the MakerBench Arena Studio web interface (Issue #696).

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────╮
│ --run-dir             TEXT     Initial run directory to load in Arena Studio.                    │
│ --host                TEXT     Bind host. [default: 127.0.0.1]                                   │
│ --allow-remote                 Allow binding Arena Studio to a non-loopback interface.           │
│ --allow-live                   Allow explicit live arena launches from Studio (may invoke        │
│                                provider CLIs).                                                   │
│ --port                INTEGER  Bind port. [default: 8080]                                        │
│ --registry            TEXT     Arena registry JSON path.                                         │
│                                [default: tasks/code_cad_arena/registry.json]                     │
│ --help                         Show this message and exit.                                       │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
```

- The server binds `127.0.0.1` by default. A non-loopback `--host` is refused
  unless `--allow-remote` is passed.
- Even with `--allow-remote`, a loopback bind keeps the DNS-rebinding guard: only
  `127.0.0.1` / `localhost` Host headers are accepted.
- Without `--allow-live`, every launch from Studio is a zero-token `--stub` dry
  run. Live, provider-backed launches need `--allow-live` at startup.
- `--run-dir` is optional. Studio discovers runs under `runs/code_cad_arena/`
  (and `arena_gen/`) either way.

## Windows/RDP launch

From a Windows desktop (including over RDP), `scripts\windows\start-arena-studio.ps1`
starts Arena Studio inside WSL and opens it in the default browser, without needing a
terminal open on both sides of the WSL boundary:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\windows\start-arena-studio.ps1
```

- **Self-locating**, the same way `run-nightly-cad-arena.ps1` is: it derives the
  repo root from its own location via `$PSScriptRoot` + `wslpath`, unless `-RepoWsl`
  is passed.
- **Waits for `/api/health`** to answer before opening the browser tab.
- **Loopback-only by construction:** there is no `-Host` parameter and it never
  passes `--allow-remote`, so Studio started this way can't be reached from another
  machine.
- **Options:** `-Port <n>` uses a port other than 8080. `-NoBrowser` starts the
  server without opening a tab.
- **Stopping:** press Ctrl+C in that PowerShell window. A `finally` block stops and
  removes the server job, then kills the exact loopback `arena studio` process inside
  WSL, so nothing is left holding the port.

> **Verified on a live Windows desktop (#743):** launch, the health check, opening
> the browser, and Ctrl+C cleanup (no server process left in WSL, nothing left on
> the port). A contract test also pins the cleanup.

## Finding your way around

The rail on the left lists every screen. The header shows the run you're working
on, a **Voting as** name (remembered in this browser), and server status.

- Each screen has its own address (`#/<screen>/<run>`), so the back button and
  bookmarks work.
- Moving to a screen puts keyboard focus on its heading.
- A **Skip to content** link is the first thing `Tab` reaches.
- Light and dark follow your system theme; both meet WCAG AA contrast.

| Screen | Address | What it's for |
| --- | --- | --- |
| **Runs** | `#/runs/<run>` | Every discovered run, with a summary of the chosen one and links to vote on it, analyse it or compare it. |
| **Blind voting** | `#/vote/<run>` | Vote on anonymous A/B pairs from a run. See [Blind voting](#blind-voting). |
| **Nightly cockpit** | `#/nightly` | A read-only view of the nightly CAD queue and its lease. |
| **Morning review** | `#/morning`, `#/morning/<job>` | Vote blind on a finished nightly bundle. |
| **Launch** | `#/launch` | Pick instruments, approve reference images, start a dry run and watch its log; nightly preflight. |
| **Agreement analytics** | `#/analytics/<run>` | Human Elo with intervals, how people agree with the objective checks, outliers, export. |
| **Compare runs** | `#/compare?a=<run>&b=<run>` | Two runs side by side and the entrants rated in both. |
| **DoE matrix** | `#/doe` | Design an experiment matrix, check its cost against a budget, write a nightly queue. |

Every screen has loading, empty and error states. An error always shows the
server's own message and a **Try again** button; it never shows stale data as if
it were current.

## Blind voting

Each pair shows two anonymous candidates, **A** and **B**, side by side. Under
each is a short problem checklist, and a vote bar sits below both.

**Viewer.** Every candidate starts on a 24-frame turntable: pre-rendered frames
shown as plain images, so it works with no WebGL at all.

- **Turn it:** drag it, or with the turntable focused use `←`/`→` (or `,`/`.`).
- **Pause:** `Space` pauses or resumes it, and it also pauses while you hover or
  focus it.
- **Loading:** frames preload with a visible progress count.
- **3D orbit:** offered only when the browser really has WebGL2. `V` switches
  between the viewers. See [Zero-WebGL fallback](#zero-webgl-fallback).

**Keyboard shortcuts.** Press `?` or use **Shortcuts** for the same list.

| Key | Action |
| --- | --- |
| `A` / `L` | Candidate A is better |
| `B` / `R` | Candidate B is better |
| `T` / `D` | Draw |
| `S` | Skip this pair for now |
| `U` | Undo your last vote |
| `V` | Switch viewer (turntable / 3D orbit) |
| `1` `2` `3` | Flag a problem on A: missing critical parts / misaligned assembly / wrong proportions |
| `Shift+1` `Shift+2` `Shift+3` | The same flags on B |
| `?` | Open the shortcut help |

Rules for the keys:
- **Arrow keys never vote.** They only turn a focused turntable.
- **Shortcuts are ignored** while you type in a field, while a modifier key (Ctrl,
  Alt, Cmd) is held, and while the help dialog is open.
- **Buttons stay focusable while busy.** They're marked unavailable rather than
  disabled, and after an undo, focus returns to **A is better**.

**Skip** moves a read-only cursor to another unvoted pair. It never changes the
vote logs or the queue.

**Undo.**
- **Where:** after a vote, an **Undo** offer stays on screen for about 8 seconds.
  That window is a convenience only; `U` still undoes your last vote after it closes.
- **What it writes:** a retraction record appended to `votes.blind.jsonl` and
  `votes.revealed.jsonl`. Original vote lines are never rewritten or removed, and
  the pair becomes votable again.
- **Effect on ratings:** Elo and agreement replay retractions by
  `(pair_id, voter_id)` before counting, so an undone vote doesn't count.

**After the vote.** Once the server has saved your vote, **Your last vote, revealed**
shows which models A and B were, with their objective results and any judge verdict
already on disk. Studio never runs a judge. The reveal is scoped to you: it
appears only for pairs *you* have voted on, never for a pair another voter rated
first.

## Zero-WebGL fallback

Interactive 3D is strictly an extra. A GPU-less machine, or a Windows RDP session
that loses its WebGL context mid-session, still gets a working vote stage:

- **Turntable by default.** Nothing about voting needs WebGL.
- **3D only with WebGL2.** **3D orbit** is offered only after a real WebGL2 check
  passes. `<model-viewer>` is loaded only the first time you pick 3D orbit, never
  up front, so a no-WebGL browser never even requests it.
- **Falls back on failure.** If the 3D viewer reports an error, including a lost
  WebGL context (which `<model-viewer>` re-dispatches as its own `error` event),
  the candidate returns to the turntable and 3D is switched off.

## Anonymity model

Votes are blind: nothing that identifies a model reaches the page before your vote
is saved.

- **Opaque pairs.** Pairs carry an opaque `pair_id` and `left`/`right` sides, never
  the `trial_id`, `model_id` or entrant name.
- **Aliased assets.** Every image, frame and model file a blind screen loads comes
  from a content-addressed alias under `vote_pages/blind/`, never from the raw
  artifact path, which would name the entrant.
- **Reveal after the vote.** Identity is written to `votes.revealed.jsonl` and shown
  only after the vote is recorded.
- **Blind screens don't load the run list.** Blind voting and Morning review never
  request `/api/runs` (it lists entrants) or the nightly cockpit (its budget charges
  name entrants). Their header shows the run name without the run picker.
- **Client check.** As defence in depth, the browser also refuses to render a pair
  that carries identity fields or a non-blind asset URL. It reports only field
  names, never their values.

The browser tests check this directly, before the vote: they search the DOM,
every attribute, the title, URL, loaded resources, storage, the console and every
API response for the fixture's model and trial names.

## Launch and preflight

- **Instruments.** The registry catalog, with a family filter.
- **Reference images.**
  - An instrument that competes on its reference image needs that image approved.
    **Inspect** shows the exact image; approve it or withdraw approval.
  - Approval is bound to the file's sha256, so replacing the image withdraws it.
  - With no image, Studio shows the `agy -p` prompt to generate one and where to
    save it.
- **Start a run.**
  - Enter entrant ids. Each shows a cost badge: "$0 subscription" for
    subscription CLI lanes, a metered estimate only when cost history exists, or
    "Cost unknown".
  - Choose a context tier, run name, seed and whether it is live.
  - **Start** stays unavailable, with the reasons listed, until every selected
    image-tier instrument is approved. The server enforces the same gate.
- **Dry runs and live runs.** A dry run uses the zero-token `--stub` generator. On a
  server started without `--allow-live`, a live request is refused and the page says
  to restart with `--allow-live`.
- **Runs started here.** Each appears with its status and a live log over
  server-sent events. When the run finishes, the stream closes and the final log
  tail stays on screen.
- **Nightly preflight.** A GO / NO-GO verdict, which secrets are present (never
  their values), lock state, queued jobs and paths, all redacted by the server.

## Agreement analytics and Compare

**Agreement analytics** (`#/analytics/<run>`):

- **Human Elo.**
  - Ratings carry 95% bootstrap interval bars on one shared scale.
  - Ratings from fewer than 5 games are marked **Provisional**.
  - Entrants with no games are listed but not ranked.
- **People versus the objective checks.**
  - Spearman ρ with a plain-language reading, and the server's caveat when there
    are too few entrants for ρ to mean much.
  - A judge matrix when judge votes exist.
  - A scatter of Human Elo against pass rate. Every point is keyboard-focusable and
    drives a live readout, and a table holds the same numbers.
- **Outliers.** Entrants whose human and objective ranks differ by 2 or more.
- **By instrument family.** A picker that shows that family's leaderboard and ρ.
- **Export.**
  - **Export winners…** first lists every `instruments/<id>/winner.scad` it will
    overwrite, and any ids the server will refuse. Nothing is written until you
    confirm.
  - **Open the Markdown report** opens the same data as Markdown, with every entrant
    name escaped.

**Compare runs** (`#/compare?a=<run>&b=<run>`) shows two runs' facts and leaderboards
side by side, and the entrants rated in both.
- A warning appears if both sides are the same run.
- If one side fails to load, only that side shows the error.

## DoE matrix

- **Design.**
  - Pick instruments, entrants, levels L1–L4, context tiers (blind, image) and seeds.
  - The preview updates shortly after you stop editing: cell count, nightly jobs,
    known cost and known time. With no timing history it says so rather than
    showing "0 s".
- **Budget what-if.** Set a per-job budget with the field or the slider. Each job
  reads **within**, **over**, or **unknown cost**. Unknown-cost jobs are never
  counted as affordable, however large the budget.
- **Unknown-cost entrants need a ceiling.** Any entrant without cost history needs
  a positive USD-per-trial ceiling before the queue can be written. Zero is refused,
  because the nightly budget guard treats `0` as "no cap".
- **Skips.** Instruments without an approved reference image are listed before you
  write; the server's actual skip list is shown after.
- **Write the nightly queue.** This writes only
  `runs/code_cad_arena/<run>/doe_queue.json`. Nothing runs.

## Nightly cockpit and Morning review

**Nightly cockpit** (`#/nightly`) is strictly read-only. It sends GET requests
only, never takes the nightly lease, and never changes the queue file.

- **Lease.** In plain words ("No lease: no nightly run is active", or "A nightly run
  holds the lease"), with the process id, heartbeat age and lock-file path.
- **Jobs.**
  - Each job's status.
  - A **Stalled** badge for a job marked running while no lease is active.
  - The budget replayed from its run directory, e.g. "$1.50 of $5.00 spent", with
    charges, halts and violations.
- **Refresh.** Automatic, every 10 seconds, only while a lease is active or a job
  is running.
- **Where it reads from.** `runs/nightly-cad-queue.json`. If that file is missing,
  the page explains it.

**Morning review** (`#/morning`) lists nightly jobs whose morning bundle is
finished (status votable, `morning-summary.json` written), with valid and failed
candidate counts and cost.
- **Review blind** opens the same vote stage as Blind voting.
- **No undo.** Morning review has no undo route, so the stage offers no Undo and
  `U` does nothing.

## Security model

- **Loopback by default.**
  - `--host` defaults to `127.0.0.1`, and a non-loopback host needs `--allow-remote`.
  - A `TrustedHost` guard rejects DNS-rebinding requests whose `Host` isn't
    `127.0.0.1` or `localhost`.
- **Same-origin writes.** Every `POST` is checked against the page's own origin;
  anything else gets `403 Cross-origin POST refused`. There is no wildcard CORS.
- **Paths stay inside their roots.**
  - Run ids are looked up through discovery, never taken as filesystem paths.
  - `/runs/<id>/vote_pages/…` and morning assets resolve only inside that run's
    `vote_pages/`.
  - The nightly queue path must be under `runs/`, and so must every job's
    `run_dir` before Studio reads its budget or morning summary.
  - Task ids must match `[a-z0-9][a-z0-9_-]*` even when the registry lists them. A
    reference image must resolve inside `tasks/` or `instruments/`.
  - Export targets stay under `instruments/`.
- **No raw HTML.** No Studio code uses `innerHTML`, `outerHTML`,
  `insertAdjacentHTML`, `dangerouslySetInnerHTML` or `document.write`. Registry
  names, queue text and entrant ids always render as text. Browser tests feed hostile
  `<img onerror>` strings through every screen that shows such data.
- **Offline and pinned.** The vendored modules are checked against sha256s in
  `static/vendor/VENDOR.json`, and nothing shipped references the network.
- **Secrets.** Preflight reports only whether each secret is present, never its
  value, and host paths are redacted from API responses.

## Checking the UI in a real browser

Playwright isn't a project dependency. To run the browser tests locally:

```
pip install "playwright==1.62.0"
python -m playwright install chromium
ARENA_STUDIO_REQUIRE_BROWSER=1 python -m pytest -q tests/test_arena_studio_browser*.py
```

- **Screenshots.** `ARENA_STUDIO_SCREENSHOTS=<dir>` keeps them.
- **Full flow.** `tests/test_arena_studio_browser_e2e.py` walks every screen in one
  session, with and without WebGL, and adds a dark-theme screenshot of each screen.
- **Why the require flag.** `ARENA_STUDIO_REQUIRE_BROWSER=1` turns a missing
  Playwright or Chromium into a failure instead of a skip.
- **CI.** The non-required, path-filtered `studio-browser` GitHub Actions job runs
  these tests and uploads the screenshots as the `studio-screens` artifact.
- **WSL.** Under WSL, Chromium may never paint a frame, so screenshots hang. Pass
  `ARENA_STUDIO_BROWSER_ARGS="--disable-gpu --disable-software-rasterizer"`. That
  also removes WebGL, so locally both modes show the zero-WebGL path; WebGL-on
  evidence comes from CI.

## Troubleshooting

- **Studio won't start: "uvicorn is required to run Arena Studio".** Install the
  studio dependencies (`pip install uvicorn`, or the pinned `requirements.lock`).
- **"Refusing a non-loopback Arena Studio host without --allow-remote."** You passed
  a `--host` other than loopback. Use `127.0.0.1`, or add `--allow-remote` if you
  really mean to expose it.
- **There's no 3D orbit option.** Expected on a machine or RDP session without
  WebGL2: the turntable is the viewer there. If 3D switched itself off mid-session,
  the WebGL context was lost.
- **Starting a live run says to restart with `--allow-live`.** The server was started
  without it, so it only starts `--stub` dry runs.
- **"Cross-origin POST refused" (403).** Something is posting to Studio from another
  origin, such as a proxy that rewrites `Origin`. Open Studio directly.
- **The Nightly cockpit says the queue is missing.** Studio reads
  `runs/nightly-cad-queue.json` under the repo root, the file the nightly CAD
  task uses (see `docs/NIGHTLY_CAD_TASK.md`). The DoE screen doesn't create it: DoE writes a
  separate `doe_queue.json` inside its run.
- **Ctrl+C in the Windows launcher left something running.** Report it on #743.
  Meanwhile `wsl.exe -d Ubuntu -- pkill -f "makerbench.cli arena studio"` stops the
  server.
