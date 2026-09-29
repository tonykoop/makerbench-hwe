# Quickstart: run one arena round in ten minutes

No Windows, SolidWorks, Fusion or API key needed. Three rungs; each stands alone
once rung 1's install is done. Times are from a clean Ubuntu (WSL2) box with Python 3.12.

## Before you start

- Python 3.10 or newer (tested on 3.12.3) with `venv`, and `git`.
- OpenSCAD on your PATH: `sudo apt-get install openscad` (Ubuntu; tested 2021.01),
  `brew install --cask openscad` (macOS), `winget install OpenSCAD.OpenSCAD` (Windows).
- Not needed for this quickstart: `xvfb`, Bubblewrap (`--sandboxed-compile` is off by default),
  any API key.

## Rung 1: stub round (about 1 minute, zero tokens)

```bash
git clone https://github.com/tonykoop/makerbench-hwe.git
cd makerbench-hwe
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.lock                    # ~25 s
pip install --no-deps -e ".[dev]"                   # ~6 s
python -m makerbench.cli arena run --run-dir runs/code_cad_arena/quickstart-stub \
  --instruments ocarina --models stub-a,stub-b --stub
```

Expected (about 2 s):

```
arena matrix: 1 instruments x 1 seeds x 1 reps x 2 models = 2 trials
summary: {"scored": 2}
   Objective scoreline (mean pass-rate)
 entrant   pass-rate   trials
 stub-a       1.000        1
 stub-b       1.000        1
```

That table is the objective scoreline: the share of trials whose generated CAD compiled
and passed the automatic checks. The stub entrants are canned generators, so both score
1.000; the point is that the whole pipeline (generate, compile, grade, score) works on your machine.
Files land in `runs/code_cad_arena/quickstart-stub/` (`objective_scoreline.json`, `run_log.json`, `gen/`, `render/`).

Optional pipeline check on the benchmark itself: `makerbench reproduce-demo` (about 2 s) ends with
`PASS reproduced the reference result`.

## Rung 2: real model, free lane (1 to 2 minutes per model)

This swaps the stub for a real model driven through a subscription CLI you are already logged in to.
OpenSCAD compiles and grades what the model writes. Entrant ids start with the CLI they use:
`claude-code-<model>` (the `claude` CLI), `codex-<model>` (`codex`), `gemini-<model>` (`gemini`).

```bash
python -m makerbench.cli arena run --run-dir runs/code_cad_arena/quickstart-real \
  --instruments ocarina --models claude-code-haiku --timeout-s 400
```

Expected (measured 1 m 04 s and 1 m 55 s on two runs):

```
arena matrix: 1 instruments x 1 seeds x 1 reps x 1 models = 1 trials
summary: {"scored": 1}
 entrant             pass-rate   trials
 claude-code-haiku       1.000        1
```

A real model can and sometimes will score below 1.000; one trial is a smoke test, not a result.
If you have no subscription CLI, skip this rung: everything else works without it.
This uses your subscription allowance, not per-token billing. Metered API entrants (for example
`openrouter-*` or `agents/anthropic_agent.py`) are out of scope here.

## Rung 3: Arena Studio (about 1 minute)

```bash
python -m makerbench.cli arena studio --port 8080
```

Expected: `MakerBench Arena Studio running at http://127.0.0.1:8080/`. Leave it running and open
that address in a browser. Studio finds runs under `runs/code_cad_arena/`, so the stub run from rung 1
(and rung 2 if you ran it) is already listed on the **Runs** screen.

- **Runs** (left rail): pick `quickstart-stub`; the summary shows its entrants and trial counts,
  with links to vote on it, analyse it or compare it.
- **Blind voting**: vote on anonymous A/B pairs, then see the Elo and agreement analytics.
  A single voter's Elo is only a demo of the mechanics, not a ranking.
- **Launch**: start another dry run (zero-token stub) and watch its log. Live, provider-backed
  runs need `--allow-live` at startup; leave it off for the quickstart.

Full screen-by-screen guide: [arena-studio.md](arena-studio.md). Stop the server with Ctrl+C.

### Matchups: vary one axis

TODO: write this section once PRs #828 and #830 are merged. Nothing here is documented until it is on `main`.

## Out of scope for this quickstart

- Windows-only paths: `scripts\windows\start-arena-studio.ps1`, `.ps1` wrappers.
- SolidWorks and Fusion backends (`--backend solidworks|fusion|solidworks-live|fusion-live`).
- Paid or metered APIs (OpenRouter, Anthropic/OpenAI keys) and the frontier re-run.
- `makerbench selftest --all` (needs the private oracle submodule) and `--sandboxed-compile` (needs Bubblewrap).
