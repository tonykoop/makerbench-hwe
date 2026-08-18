<#
.SYNOPSIS
Registers the Windows scheduled task for the MakerBench nightly CAD arena.

.DESCRIPTION
Canonical invocation (run from an elevated PowerShell in the canonical
checkout C:\Users\Tony\Documents\GitHub\makerbench_ecosystem\makerbench-hwe):

  powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File scripts\windows\install-nightly-cad-task.ps1 `
    -RunnerScript C:\Users\Tony\Documents\GitHub\makerbench_ecosystem\makerbench-hwe\scripts\windows\run-nightly-cad-arena.ps1 `
    -QueueWsl /mnt/c/.../queue.json `
    -OutputRootWsl /mnt/c/.../runs `
    -SecretsWsl /mnt/c/.../nightly-cad-secrets.env `
    -Disabled

-RunnerScript is validated at registration time; a nonexistent path aborts
instead of baking a dead action into the task. See docs/NIGHTLY_CAD_TASK.md.
#>
param(
    [string]$TaskName = "makerbench-nightly-cad",
    [string]$StartTime = "00:30",
    [Parameter(Mandatory = $true)][string]$RunnerScript,
    [Parameter(Mandatory = $true)][string]$QueueWsl,
    [Parameter(Mandatory = $true)][string]$OutputRootWsl,
    [Parameter(Mandatory = $true)][string]$SecretsWsl,
    [string]$Distro = "Ubuntu",
    [switch]$RequireACPower,
    [switch]$Disabled
)

$ErrorActionPreference = "Stop"

# Validate the runner script at registration time so we never bake a dead
# path into the scheduled task action (the legacy-checkout failure mode).
if (-not (Test-Path -LiteralPath $RunnerScript -PathType Leaf)) {
    throw ("RunnerScript not found: '$RunnerScript'. Pass the absolute path to " +
        "scripts\windows\run-nightly-cad-arena.ps1 inside the canonical checkout " +
        "(see docs/NIGHTLY_CAD_TASK.md).")
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $RunnerScript + '"'),
    "-Distro", $Distro,
    "-QueueWsl", ('"' + $QueueWsl + '"'),
    "-OutputRootWsl", ('"' + $OutputRootWsl + '"'),
    "-SecretsWsl", ('"' + $SecretsWsl + '"')
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 7) `
    -AllowStartIfOnBatteries:(-not $RequireACPower.IsPresent) `
    -DontStopIfGoingOnBatteries:(-not $RequireACPower.IsPresent)
# InteractiveToken is intentional: Fusion and SolidWorks automation requires
# Tony's logged-in desktop session. A locked session is fine; logged-off is not.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Run one queued MakerBench musical-instrument CAD arena at 12:30 AM." `
    -Force | Out-Null

if ($Disabled) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
}

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, TaskPath
