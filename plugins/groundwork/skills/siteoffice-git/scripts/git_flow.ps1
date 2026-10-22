# git_flow.ps1 - launcher for the Siteoffice git flow engine (sync\git_flow.py).
# Pure ASCII, Windows PowerShell 5.1 compatible. Usage, from inside any Siteoffice repo:
#   git_flow.ps1 <start <slug> | save | check | propose | land | abort> [--apply] [other engine args]
# Finds the nearest siteoffice.json upward from the current directory, finds the engine, runs
#   python <engine> <args> [--harness claude-code] --repo <root>
# Receipt harness: an explicit --harness in <args> wins, then SITEOFFICE_HARNESS (the engine reads
# it), then claude-code when CLAUDECODE=1 (a Claude Code session). Otherwise the engine logs unknown.
# and passes the exit code through. A refusal by this launcher exits 2 (the engine's "refused").
$ErrorActionPreference = 'Stop'

function Stop-Refused {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
    exit 2
}

function Find-Root {
    $dir = (Get-Location).ProviderPath
    while ($dir) {
        if (Test-Path -LiteralPath (Join-Path $dir 'siteoffice.json') -PathType Leaf) { return $dir }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

function Resolve-Engine {
    # A candidate may name the engine file or the folder that holds it.
    param([string]$Candidate)
    if (-not $Candidate) { return $null }
    if (Test-Path -LiteralPath $Candidate -PathType Container) { $Candidate = Join-Path $Candidate 'git_flow.py' }
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $Candidate).ProviderPath }
    return $null
}

$root = Find-Root
if (-not $root) { Stop-Refused ('No siteoffice.json found at or above ' + (Get-Location).ProviderPath + '. Run this from inside a Siteoffice repo.') }

$manifestEngine = $null
try {
    $manifest = Get-Content -LiteralPath (Join-Path $root 'siteoffice.json') -Raw | ConvertFrom-Json
    $value = $manifest.settings.git.engine
    if ($value -is [string] -and $value.Trim()) {
        if ([System.IO.Path]::IsPathRooted($value)) { $manifestEngine = $value } else { $manifestEngine = Join-Path $root $value }
    }
} catch {
    $manifestEngine = $null
}

$engine = $null
# Last candidate: the engine the Groundwork plugin ships (engine\sync, three folders up from here).
$bundled = Join-Path $PSScriptRoot '..\..\..\engine\sync\git_flow.py'
foreach ($candidate in @($env:SITEOFFICE_SYNC_DIR, $manifestEngine, (Join-Path $root 'sync\git_flow.py'), $bundled)) {
    $engine = Resolve-Engine $candidate
    if ($engine) { break }
}
if (-not $engine) { Stop-Refused 'Engine not found: set SITEOFFICE_SYNC_DIR or settings.git.engine, or install the groundwork plugin, which ships one.' }

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Stop-Refused 'python was not found on PATH: install Python 3.8 or newer.' }

$harnessArgs = @()
$hasHarness = @($args | Where-Object { "$_" -like '--harness*' }).Count -gt 0
if (-not $hasHarness -and -not $env:SITEOFFICE_HARNESS -and $env:CLAUDECODE -eq '1') {
    $harnessArgs = @('--harness', 'claude-code')
}

& python $engine @args @harnessArgs --repo $root
exit $LASTEXITCODE
# END git_flow.ps1
