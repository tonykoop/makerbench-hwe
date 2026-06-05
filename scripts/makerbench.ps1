param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $MakerBenchArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPython = Join-Path $repoRoot ".venv-win\Scripts\python.exe"

if (Test-Path $venvPython) {
    & $venvPython -m makerbench.cli @MakerBenchArgs
    exit $LASTEXITCODE
}

& py -3 -m makerbench.cli @MakerBenchArgs
exit $LASTEXITCODE
