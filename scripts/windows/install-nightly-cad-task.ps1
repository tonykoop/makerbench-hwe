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
