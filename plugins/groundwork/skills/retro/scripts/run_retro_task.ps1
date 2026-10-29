# Runs the monthly retro scan once, with no prompts, and proves the result by its exit code.
# Runs in: a hidden PowerShell started by run_retro_hidden.vbs (see register_retro_task.ps1).
# You can also run it by hand: powershell.exe -NoProfile -File <this file> -Root <your root>
#
# Exit codes: 0 only when retro_report.py succeeded. 4 when retro\config.json is missing
# (run retro_config.py init first, this script never asks questions). 1 on an unexpected
# error. Anything else is the exit code of retro_report.py itself.
# One line per run goes to <Root>\retro\scheduled_runs.log. The line holds the time, the
# word OK or FAILED and the exit code, never any transcript content.
# retro_report.py collects, estimates and writes the report in one call, so this script
# calls only that one script and does not collect a second time.
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$Python = 'py',
    [string]$ReportScript = (Join-Path $PSScriptRoot 'retro_report.py')
)
$ErrorActionPreference = 'Stop'

# A trailing backslash (tab completion) would escape the closing quote of a quoted argument.
# Trim it, except on a bare drive root, and double it when one has to stay.
function Get-CleanRoot([string]$Path) {
    if ($Path.Length -gt 3) { $Path = $Path.TrimEnd(([char]92), '/') }
    return $Path
}
function Format-Arg([string]$Value) {
    if ($Value.EndsWith([string][char]92)) { $Value = $Value + [char]92 }
    return '"' + $Value + '"'
}
$Root = Get-CleanRoot $Root
$retroDir = Join-Path $Root 'retro'
$log = Join-Path $retroDir 'scheduled_runs.log'

function Write-RunLog([string]$Line) {
    $null = New-Item -ItemType Directory -Force -Path $retroDir
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    Add-Content -Path $log -Value ('{0} {1}' -f $stamp, $Line) -Encoding ASCII
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $retroDir 'config.json'))) {
        Write-RunLog 'retro monthly scan FAILED exit 4: config missing, run retro_config.py init first'
        exit 4
    }
    $argList = @()
    if ((Split-Path -Leaf $Python) -match '^py(\.exe)?$') { $argList += '-3' }
    $argList += @('-I', (Format-Arg $ReportScript), '--root', (Format-Arg $Root))
    $outFile = Join-Path $retroDir 'last_run.out.txt'
    $errFile = Join-Path $retroDir 'last_run.err.txt'
    try {
        $p = Start-Process -FilePath $Python -ArgumentList $argList -Wait -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    } catch {
        Write-RunLog ('retro monthly scan FAILED exit 1: python not found or could not start ({0})' -f $_.Exception.GetType().Name)
        exit 1
    }
    $rc = [int]$p.ExitCode
    if ($rc -eq 0) {
        Write-RunLog 'retro monthly scan OK exit 0'
    } else {
        Write-RunLog ('retro monthly scan FAILED exit {0}: see last_run.err.txt' -f $rc)
    }
    exit $rc
} catch {
    Write-RunLog ('retro monthly scan FAILED exit 1: {0}' -f $_.Exception.GetType().Name)
    exit 1
}
