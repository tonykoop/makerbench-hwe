# Nightly CAD Arena — Windows scheduled task

The nightly musical-instrument CAD arena (#648) is launched by a Windows
scheduled task that shells into WSL and runs
`python3 -m makerbench.cli arena overnight`. Two PowerShell scripts own the
Windows side, both in `scripts/windows/`:

| Script | Role |
| --- | --- |
| `run-nightly-cad-arena.ps1` | The task action: validates paths, then runs the arena in WSL. |
| `install-nightly-cad-task.ps1` | Registers (or re-registers) the scheduled task. |
| `check-nightly-cad-paths.sh` | WSL-side fail-fast path preflight used by the runner. |

> **Operational note:** the scheduled task is currently **Disabled** and must
> stay Disabled until the arena GO review passes. These scripts are repo-side
> plumbing only; do not enable the task as part of a repo change.

## Canonical checkout

The canonical repo checkout is:

- Windows: `C:\Users\<you>\Documents\GitHub\makerbench_ecosystem\makerbench-hwe`
- WSL: `/mnt/c/Users/<you>/Documents/GitHub/makerbench_ecosystem/makerbench-hwe`

The pre-2026-07 `Documents/GitHub/makerbench-hwe` checkout is dead. The runner
no longer hardcodes any checkout: when `-RepoWsl` is not supplied it derives
the repo root from its own location (`scripts/windows/` → repo root, converted
with `wslpath`), so moving the checkout again cannot strand the task — as long
as the scheduled task action points at the runner inside the live checkout.

## Canonical invocation

Run the arena once, manually (from the canonical checkout):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\windows\run-nightly-cad-arena.ps1 `
  -QueueWsl /mnt/c/Users/<you>/Documents/GitHub/makerbench_ecosystem/makerbench-hwe/runs/nightly-cad-queue.json `
  -OutputRootWsl /mnt/c/Users/<you>/Documents/GitHub/makerbench_ecosystem/makerbench-hwe/runs `
  -SecretsWsl <path to nightly-cad secrets env file>
```

Register the scheduled task (elevated PowerShell; keep `-Disabled` until GO):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\windows\install-nightly-cad-task.ps1 `
  -RunnerScript C:\Users\<you>\Documents\GitHub\makerbench_ecosystem\makerbench-hwe\scripts\windows\run-nightly-cad-arena.ps1 `
  -QueueWsl <queue json, WSL path> `
  -OutputRootWsl <output root, WSL path> `
  -SecretsWsl <secrets env file, WSL path> `
  -Disabled
```

## Fail-fast guarantees

- `run-nightly-cad-arena.ps1` validates `RepoWsl`, `QueueWsl`, `SecretsWsl`,
  `OutputRootWsl`, and `InstrumentsRootWsl` inside WSL **before** launching
  the arena, via `check-nightly-cad-paths.sh`. Every missing path is reported
  with its parameter name; any missing path aborts the run.
- `install-nightly-cad-task.ps1` refuses to register a task whose
  `-RunnerScript` does not exist, so a dead path can no longer be baked into
  the task action.
- `tests/test_nightly_cad_scripts.py` lints `scripts/windows/` against the
  legacy checkout path and exercises the preflight RED (bogus path) and green
  (real paths) from WSL.

## Fixing a design step by step

The nightly driver builds hands-off in one pass. Some fixes need to go step by step on a complex
assembly: measure, adjust, re-measure, render. For those, open an **interactive** session wired
the same way as the driver:

- the CAD connector's MCP tools are pre-approved, for that one server only;
- the bridge env goes by name (`env_vars` / `${NAME:-}`), so no token is on disk or in argv;
- shell commands stay sandboxed (Codex `workspace-write`), and other Claude tools still prompt;
- nothing is written to `~/.codex/config.toml` or to the Claude settings.

The interactive Claude session runs without `--restricted`, so a supervised session can run small
measurement scripts under your own Claude permission settings. The unattended nightly driver keeps
`--restricted`.

```bash
# sambuca r2: make the neck meet the hull, in SolidWorks, with Codex
scripts/live-cad-session.sh --cad solidworks --runtime codex \
  --workdir /mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-nightly/runs/<run-id> \
  --prompt-file neck-step1.md
# same with Claude Code in Fusion: --cad fusion --runtime claude  (default model claude-opus-5-5)
```

`neck-step1.md` holds only the first step, for example: *"Measure the gap between the neck heel
and the hull's bow face and report it in mm. Change nothing."* Give each next step in the
session: move the neck so the heel touches the hull, re-measure (gap 0, no interpenetration),
`capture_view`, then save the native file.

The launcher reads `--secrets` (default `StudioPipeline-Pilot/arena-nightly/makerbench-nightly.env`)
in its own process and never prints it. It refuses to start if the bridge does not answer an
authenticated ping, or if the connector's MCP shim cannot ping through the driver's own launch
path. `--dry-run` runs those checks and prints the argv. The same command is
`python -m makerbench.cli arena live-cad-session`.
