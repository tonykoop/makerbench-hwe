param(
    [string]$Distro = "Ubuntu",
    [string]$RepoWsl = "/mnt/c/Users/Tony/Documents/GitHub/makerbench-hwe",
    [Parameter(Mandatory = $true)][string]$QueueWsl,
    [Parameter(Mandatory = $true)][string]$OutputRootWsl,
    [Parameter(Mandatory = $true)][string]$SecretsWsl,
    [string]$InstrumentsRootWsl = "/mnt/c/Users/Tony/Documents/GitHub/instruments"
)

$ErrorActionPreference = "Stop"
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
