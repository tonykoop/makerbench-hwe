<#
.SYNOPSIS
Runs one queued MakerBench nightly musical-instrument CAD arena via WSL.

.DESCRIPTION
Canonical invocation (from the canonical checkout
C:\Users\<you>\Documents\GitHub\makerbench_ecosystem\makerbench-hwe):

  powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File scripts\windows\run-nightly-cad-arena.ps1 `
    -QueueWsl /mnt/c/.../queue.json `
    -OutputRootWsl /mnt/c/.../runs `
    -SecretsWsl /mnt/c/.../nightly-cad-secrets.env

Self-locating: when -RepoWsl is not supplied, the repo root is derived from
this script's own location (scripts\windows\ -> repo root, converted with
wslpath), so a checkout move never strands the scheduled task again. Pass
-RepoWsl explicitly only to run against a different checkout.

All required WSL paths are validated before wsl.exe launches the arena; a
missing path aborts with an actionable per-parameter error. See
docs/NIGHTLY_CAD_TASK.md.
#>
param(
    [string]$Distro = "Ubuntu",
    [string]$RepoWsl = "",
    [Parameter(Mandatory = $true)][string]$QueueWsl,
    [Parameter(Mandatory = $true)][string]$OutputRootWsl,
    [Parameter(Mandatory = $true)][string]$SecretsWsl,
    [string]$InstrumentsRootWsl = "/mnt/c/Users/Tony/Documents/GitHub/instruments"
)

$ErrorActionPreference = "Stop"

function ConvertTo-WslPath([string]$WindowsPath) {
    $normalized = $WindowsPath -replace '\\', '/'
    $out = & wsl.exe --distribution $Distro -- wslpath -a "$normalized"
    if ($LASTEXITCODE -ne 0) {
        throw "wslpath failed for '$WindowsPath' (distro '$Distro')."
    }
    return ($out | Select-Object -First 1).Trim()
}

# Self-locate the repo root when -RepoWsl is not supplied: this script lives
# at <repo>/scripts/windows/, so the repo root is two levels up from here.
if ([string]::IsNullOrWhiteSpace($RepoWsl)) {
    $repoRootWindows = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $RepoWsl = ConvertTo-WslPath $repoRootWindows
}

# Fail fast on any missing path BEFORE launching the arena itself.
$preflight = ConvertTo-WslPath (Join-Path $PSScriptRoot "check-nightly-cad-paths.sh")
& wsl.exe --distribution $Distro -- bash "$preflight" `
    "RepoWsl=$RepoWsl" `
    "QueueWsl=$QueueWsl" `
    "SecretsWsl=$SecretsWsl" `
    "OutputRootWsl=$OutputRootWsl" `
    "InstrumentsRootWsl=$InstrumentsRootWsl"
if ($LASTEXITCODE -ne 0) {
    throw "Nightly CAD arena preflight failed: one or more required WSL paths are missing (see errors above)."
}

$command = @"
set -euo pipefail
set -a
source '$SecretsWsl'
set +a
cd '$RepoWsl'
python3 -m makerbench.cli arena overnight \
  --queue '$QueueWsl' \
  --output-root '$OutputRootWsl' \
  --instruments-root '$InstrumentsRootWsl'
"@

& wsl.exe --distribution $Distro -- bash -lc $command
if ($LASTEXITCODE -ne 0) {
    throw "MakerBench overnight arena exited with code $LASTEXITCODE"
}
