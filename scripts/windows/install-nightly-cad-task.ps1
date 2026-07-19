param(
    [string]$TaskName = "makerbench-nightly-cad",
    [string]$StartTime = "00:30",
    [Parameter(Mandatory = $true)][string]$RunnerScript,
    [Parameter(Mandatory = $true)][string]$QueueWsl,
    [Parameter(Mandatory = $true)][string]$OutputRootWsl,
    [Parameter(Mandatory = $true)][string]$SecretsWsl,
    [string]$Distro = "Ubuntu"
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
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 7) `
    -AllowStartIfOnBatteries:$false `
    -DontStopIfGoingOnBatteries:$false
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Run one queued MakerBench musical-instrument CAD arena at 12:30 AM." `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, TaskPath
