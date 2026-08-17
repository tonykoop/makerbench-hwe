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

- Windows: `C:\Users\Tony\Documents\GitHub\makerbench_ecosystem\makerbench-hwe`
- WSL: `/mnt/c/Users/Tony/Documents/GitHub/makerbench_ecosystem/makerbench-hwe`

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
  -QueueWsl /mnt/c/Users/Tony/Documents/GitHub/makerbench_ecosystem/makerbench-hwe/runs/nightly-cad-queue.json `
  -OutputRootWsl /mnt/c/Users/Tony/Documents/GitHub/makerbench_ecosystem/makerbench-hwe/runs `
  -SecretsWsl <path to nightly-cad secrets env file>
```

Register the scheduled task (elevated PowerShell; keep `-Disabled` until GO):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\windows\install-nightly-cad-task.ps1 `
  -RunnerScript C:\Users\Tony\Documents\GitHub\makerbench_ecosystem\makerbench-hwe\scripts\windows\run-nightly-cad-arena.ps1 `
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
