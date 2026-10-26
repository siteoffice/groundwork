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

function Get-FullPath {
    param([string]$Path, [string]$Base)
    if (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $Base $Path }
    $full = [System.IO.Path]::GetFullPath($Path)
    $trimmed = $full.TrimEnd('\', '/')
    if ($trimmed -eq '' -or $trimmed -match '^[A-Za-z]:$') { return $full }
    return $trimmed
}

function Test-Inside {
    param([string]$Path, [string[]]$Roots)
    foreach ($r in $Roots) {
        if (-not $r) { continue }
        if ($Path.Equals($r, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
        $prefix = $r.TrimEnd('\', '/')
        foreach ($sep in @('\', '/')) {
            if ($Path.StartsWith($prefix + $sep, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
        }
    }
    return $false
}

function Get-RealPath {
    # Where a path lands once every symlink and junction on the way is followed, walking one
    # component at a time (works on Windows PowerShell 5.1). $null when a component does not exist.
    param([string]$Path, [int]$Depth = 0)
    if ($Depth -gt 32) { return $null }
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
        $cur = [System.IO.Path]::GetPathRoot($full)
        $rest = $full.Substring($cur.Length)
        foreach ($seg in ($rest -split '[\\/]')) {
            if (-not $seg) { continue }
            $next = Join-Path $cur $seg
            $item = Get-Item -LiteralPath $next -Force -ErrorAction Stop
            if ($item.LinkType) {
                $target = @($item.Target)[0]
                if (-not $target) { return $null }
                if ($target.StartsWith('\??\')) { $target = $target.Substring(4) }
                if (-not [System.IO.Path]::IsPathRooted($target)) { $target = Join-Path $cur $target }
                $next = Get-RealPath $target ($Depth + 1)
                if (-not $next) { return $null }
            }
            $cur = $next
        }
        return $cur.TrimEnd('\', '/') + $(if ($cur -match '^[A-Za-z]:[\\/]?$') { '\' } else { '' })
    } catch {
        return $null
    }
}

$engine = $null
# Last candidate: the engine the Groundwork plugin ships (engine\sync, three folders up from here).
$bundled = Join-Path $PSScriptRoot '..\..\..\engine\sync\git_flow.py'

if ($env:SITEOFFICE_SYNC_DIR -and $env:SITEOFFICE_SYNC_DIR.Trim()) {
    $raw = $env:SITEOFFICE_SYNC_DIR
    $ignored = $null
    # Relative, drive-relative, UNC and device values are never usable, and cost no file system call.
    # A value that is only separators (a bare root) names no folder either.
    if (($raw -notmatch '^([\\/](?![\\/])|[A-Za-z]:[\\/])') -or (-not $raw.Trim('\', '/'))) {
        $ignored = 'SITEOFFICE_SYNC_DIR is not an absolute path, so it is ignored.'
    } else {
        $folders = @($root, (Join-Path $root '.claude\worktrees'))
        if (Get-Command git -ErrorAction SilentlyContinue) {
            $saved = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $listed = @(& git -C $root worktree list --porcelain 2>$null)
                foreach ($line in $listed) {
                    if ("$line".StartsWith('worktree ')) { $folders += "$line".Substring(9).Trim() }
                }
            } catch {
            } finally {
                $ErrorActionPreference = $saved
            }
        }
        $folders = @($folders | ForEach-Object { Get-FullPath $_ $root })
        $roots = @($folders)
        foreach ($f in $folders) {
            $realRoot = Get-RealPath $f
            if ($realRoot) { $roots += $realRoot }
        }
        $file = $raw
        if ($file -notmatch '\.py$') { $file = (Join-Path $raw.TrimEnd('\', '/') 'git_flow.py') }
        $file = Get-FullPath $file $root
        # The file itself when it resolves, else its folder plus its name.
        $real = Get-RealPath $file
        if (-not $real) {
            $realDir = Get-RealPath (Split-Path -Parent $file)
            if ($realDir) { $real = Join-Path $realDir (Split-Path -Leaf $file) }
        }
        if (-not $real) {
            $ignored = 'SITEOFFICE_SYNC_DIR does not resolve to a folder or file, so it is ignored.'
        } elseif ((Test-Inside $file $roots) -or (Test-Inside $real $roots)) {
            $ignored = 'SITEOFFICE_SYNC_DIR points inside this repo or one of its worktrees, so it is ignored.'
        } else {
            $engine = Resolve-Engine $real
        }
    }
    if ($ignored) { [Console]::Error.WriteLine($ignored) }
}
if (-not $engine) { $engine = Resolve-Engine $bundled }
if (-not $engine) { Stop-Refused 'Engine not found: install the groundwork plugin, which ships one, or set SITEOFFICE_SYNC_DIR to a folder outside this repo.' }

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Stop-Refused 'python was not found on PATH: install Python 3.8 or newer.' }

$harnessArgs = @()
$hasHarness = @($args | Where-Object { "$_" -like '--harness*' }).Count -gt 0
if (-not $hasHarness -and -not $env:SITEOFFICE_HARNESS -and $env:CLAUDECODE -eq '1') {
    $harnessArgs = @('--harness', 'claude-code')
}

& python $engine @args @harnessArgs --repo $root
exit $LASTEXITCODE
# END git_flow.ps1
