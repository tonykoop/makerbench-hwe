<#
.SYNOPSIS
    Windows-side watcher for the Code-CAD Arena job-dir CAD backends (#627).

.DESCRIPTION
    The arena loop runs on the WSL/Linux side and cannot drive SolidWorks or
    Fusion 360 (their kernels are Windows-only, scripted through COM / the
    Fusion API). This watcher is the Windows half of the job-dir handshake:

        Linux side (makerbench.code_cad_backends.make_job_dir_compiler)
          writes  jobs/<trial_id>/input/<source>
          writes  jobs/<trial_id>/status.json  { "state": "pending", ... }
          polls   jobs/<trial_id>/status.json  until state == done|error

        Windows side (this script)
          for each pending job:
            drive the CAD app -> rebuild the entrant -> export STL + preview PNG
            write   jobs/<trial_id>/artifacts/output.stl
            write   jobs/<trial_id>/artifacts/preview.png
            flip    status.json -> { "state": "done" }   (or "error" + "error": msg)

    THIS IS A DOCUMENTED REFERENCE STUB. The CAD-driving blocks are marked TODO
    and throw by default so nothing pretends to have compiled. It encodes the
    EXACT on-disk contract the Python side expects (see docs/CODE_CAD_BACKENDS.md)
    so a maker can fill in the SolidWorks COM / Fusion API calls for their box.
    It is never exercised in CI.

    The `-Backend` you pass here must match the backend the Python side was
    started with (`makerbench arena run --backend solidworks|fusion ...`); the
    per-job status.json also records its backend so a shared jobs dir can host
    mixed backends and each watcher services only its own.

.PARAMETER JobsRoot
    Path to the shared jobs dir. On WSL this is e.g.
    \\wsl$\Ubuntu\home\tony\...\<run-dir>\backend_jobs  (or a Windows path if the
    run dir lives under /mnt/c). Must match the Python --jobs-root.

.PARAMETER Backend
    "solidworks" or "fusion" — selects which export routine to call and which
    pending jobs to claim.

.PARAMETER PollSeconds
    Seconds between scans of the jobs dir. Default 2.

.EXAMPLE
    ./arena_windows_backend_watcher.ps1 -JobsRoot 'C:\runs\arena\r1\backend_jobs' -Backend solidworks
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $JobsRoot,
    [Parameter(Mandatory = $true)] [ValidateSet('solidworks', 'fusion')] [string] $Backend,
    [int] $PollSeconds = 2
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $JobsRoot)) {
    New-Item -ItemType Directory -Path $JobsRoot -Force | Out-Null
}

# Heartbeat: backend_preflight() reports this file as "watcher alive". Refresh it
# on every scan so a stale heartbeat is easy to spot by mtime.
$Heartbeat = Join-Path $JobsRoot 'watcher.heartbeat'

function Write-Status([string] $Path, [hashtable] $Status) {
    # Atomic publish so the polling Python side never reads a partial file.
    $tmp = "$Path.tmp"
    ($Status | ConvertTo-Json -Depth 6) | Set-Content -Path $tmp -Encoding utf8
    Move-Item -Path $tmp -Destination $Path -Force
}

function Export-SolidWorks([string] $SourcePath, [string] $StlPath, [string] $PngPath) {
    # TODO: implement with the SolidWorks COM API, e.g.:
    #   $sw = New-Object -ComObject SldWorks.Application
    #   $model = $sw.OpenDoc6($SourcePath, ...)         # open the entrant part/assembly
    #   $model.SaveAs3($StlPath, 0, 0)                  # STL export (Tools>Options sets ASCII/binary + units=mm)
    #   $model.ShowNamedView2('*Isometric', -1); $sw.SaveDocsToImage($PngPath ...)  # preview
    #   $sw.CloseDoc($model.GetTitle())
    throw "SolidWorks export not implemented — fill in Export-SolidWorks for your machine."
}

function Export-Fusion([string] $SourcePath, [string] $StlPath, [string] $PngPath) {
    # TODO: implement with the Fusion 360 API (Python add-in driven headless, or
    # a scripted command). Typical shape:
    #   app = adsk.core.Application.get(); design = app.activeProduct
    #   importManager.importToTarget(...)              # bring the entrant in
    #   exportMgr.execute(exportMgr.createSTLExportOptions(root, $StlPath))
    #   viewport.saveAsImageFile($PngPath, 800, 600)   # preview
    throw "Fusion export not implemented — fill in Export-Fusion for your machine."
}

Write-Host "arena watcher: backend=$Backend jobs=$JobsRoot (Ctrl+C to stop)"

while ($true) {
    Set-Content -Path $Heartbeat -Value (Get-Date -Format o) -Encoding utf8

    Get-ChildItem -Path $JobsRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $jobDir = $_.FullName
        $statusPath = Join-Path $jobDir 'status.json'
        if (-not (Test-Path $statusPath)) { return }

        try {
            $status = Get-Content -Path $statusPath -Raw | ConvertFrom-Json
        } catch { return }  # mid-write; retry next scan

        if ($status.state -ne 'pending') { return }
        if ($status.backend -ne $Backend) { return }  # not ours; another watcher owns it

        $artifactsDir = Join-Path $jobDir 'artifacts'
        New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null
        $sourcePath = Join-Path $jobDir $status.input
        $stlPath = Join-Path $artifactsDir 'output.stl'
        $pngPath = Join-Path $artifactsDir 'preview.png'

        Write-Host "  job $($status.trial_id): compiling via $Backend ..."
        try {
            switch ($Backend) {
                'solidworks' { Export-SolidWorks $sourcePath $stlPath $pngPath }
                'fusion'     { Export-Fusion     $sourcePath $stlPath $pngPath }
            }
            Write-Status $statusPath @{
                schema   = 'makerbench-code-cad-backend-job-v1'
                state    = 'done'
                backend  = $Backend
                trial_id = $status.trial_id
                source   = $status.source
                input    = $status.input
                artifacts = @{ stl = 'artifacts/output.stl'; preview = 'artifacts/preview.png' }
            }
            Write-Host "  job $($status.trial_id): done"
        } catch {
            Write-Status $statusPath @{
                schema   = 'makerbench-code-cad-backend-job-v1'
                state    = 'error'
                backend  = $Backend
                trial_id = $status.trial_id
                error    = $_.Exception.Message
            }
            Write-Host "  job $($status.trial_id): error — $($_.Exception.Message)"
        }
    }

    Start-Sleep -Seconds $PollSeconds
}
