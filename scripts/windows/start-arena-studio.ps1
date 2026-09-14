<#
.SYNOPSIS
Launches MakerBench Arena Studio via WSL and opens it in the Windows browser.

.DESCRIPTION
Canonical invocation (from the canonical checkout
C:\Users\<you>\Documents\GitHub\makerbench_ecosystem\makerbench-hwe):

  powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File scripts\windows\start-arena-studio.ps1

Self-locating: -RepoWsl is derived from this script's own location
(scripts\windows\ -> repo root, converted with wslpath) unless passed
explicitly, the same convention run-nightly-cad-arena.ps1 uses.

Starts `arena studio` inside WSL bound to 127.0.0.1 ONLY (loopback) — this
script never passes --allow-remote and never accepts a -Host parameter, so
Arena Studio is unreachable from any other machine on the network no matter
how it is invoked. Waits for /api/health to answer before opening the
Windows default browser at http://127.0.0.1:<port>/, so the tab never loads
before the server is ready. The WSL server process is left running in the
foreground of its own job; close the opened PowerShell/job or Ctrl+C this
script to stop it. See docs/arena-studio.md.

STATUS: written and unit-tested (path-lint + a fake-server smoke test) in
this sandbox, which has no Windows desktop or wsl.exe to launch a real
browser against — unverified on a live Windows desktop. See the
WINDOWS-GATED verification story filed against this change.
#>
param(
    [string]$Distro = "Ubuntu",
    [string]$RepoWsl = "",
    [int]$Port = 8080,
    [string]$Registry = "",
    [switch]$NoBrowser
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

$registryArg = ""
if (-not [string]::IsNullOrWhiteSpace($Registry)) {
    $registryArg = "--registry '$Registry'"
}

# Loopback ONLY (see the header comment above for the full guarantee). A
# caller cannot make this reachable from the network without editing the
# script itself.
$command = @"
set -euo pipefail
cd '$RepoWsl'
exec python3 -m makerbench.cli arena studio --host 127.0.0.1 --port $Port $registryArg
"@

$serverJob = Start-Job -ScriptBlock {
    param($DistroName, $Cmd)
    & wsl.exe --distribution $DistroName -- bash -lc $Cmd
} -ArgumentList $Distro, $command

$healthUrl = "http://127.0.0.1:$Port/api/health"
$deadline = (Get-Date).AddSeconds(30)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if ($serverJob.State -eq "Failed" -or $serverJob.State -eq "Completed") {
        Receive-Job -Job $serverJob | Write-Host
        throw "Arena Studio server process exited before becoming ready (job state: $($serverJob.State))."
    }
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # Not up yet; keep polling until the deadline.
    }
    Start-Sleep -Milliseconds 500
}

if (-not $ready) {
    Stop-Job -Job $serverJob -ErrorAction SilentlyContinue
    Receive-Job -Job $serverJob | Write-Host
    throw "Arena Studio did not become healthy at $healthUrl within 30s."
}

Write-Host "MakerBench Arena Studio running at http://127.0.0.1:$Port/ (WSL job id $($serverJob.Id))"

if (-not $NoBrowser.IsPresent) {
    Start-Process "http://127.0.0.1:$Port/"
}

Write-Host "Press Ctrl+C or run 'Stop-Job -Id $($serverJob.Id)' to stop the server."
Wait-Job -Job $serverJob | Out-Null
Receive-Job -Job $serverJob | Write-Host
