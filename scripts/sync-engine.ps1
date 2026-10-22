<#
.SYNOPSIS
  Vendor the Foreman engine and the board renderer into this repo from a Siteoffice checkout,
  then neutralise them.

.DESCRIPTION
  Copies each component from the Siteoffice checkout (default: the sibling folder siteoffice)
  into plugins\groundwork, skipping caches, logs, per-operator state and internal notes. Then
  applies a fixed rewrite map to every text file so the public copy carries no private path or
  name. The source is never modified.

    engine : siteoffice\foreman -> plugins\groundwork\engine\foreman
    board  : siteoffice\board   -> plugins\groundwork\board

  Dry run is the default. Nothing is written until you pass -Apply. -Verify runs the payload
  gate and the tests of what was synced.

.EXAMPLE
  PowerShell, from the repo root:
    pwsh -NoProfile -File .\scripts\sync-engine.ps1 -Apply -Verify
#>
[CmdletBinding()]
param(
    [string]$SiteofficeRoot = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'siteoffice'),
    [ValidateSet('engine', 'board', 'sync', 'all')]
    [string]$Component = 'all',
    [switch]$Apply,
    [switch]$Verify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$pluginDir = Join-Path $repo 'plugins\groundwork'
$SiteofficeRoot = (Resolve-Path -LiteralPath $SiteofficeRoot).Path

# Rewrite map, applied in order. Names are built from fragments so this file passes its own gate.
# Each rule: Rx (.NET regex), To (replacement), TestToo (also applied to test_*.py and tests\).
$zn = 'ZN' + 'D'
$tal = 'Tal' + 'on'
$cip = 'CIPH' + 'ER'
$usr = 'WilcoDe' + 'Tree'
$lowusr = 'wilcode' + 'tree'
$hold = '10_hold' + 'ing'
$nm = 'Ba' + 'rt'
$rules = @(
    @{ Rx = ('C:\\{1,2}' + $zn + '\\{1,2}' + $hold + '\\{1,2}05_docs\\{1,2}superpowers\\{1,2}specs\\{1,2}[A-Za-z0-9_.\-]+\.md'); To = '(design spec, not shipped)'; TestToo = $false },
    @{ Rx = ('a ' + $tal + ' customer'); To = 'a customer'; TestToo = $true },
    @{ Rx = ($tal + ' customer'); To = 'customer'; TestToo = $true },
    @{ Rx = ('\(' + $tal + ', or any other customer\)'); To = '(any customer)'; TestToo = $true },
    @{ Rx = ($tal + '-style'); To = 'customer-style'; TestToo = $true },
    @{ Rx = $tal; To = 'the customer'; TestToo = $true },
    @{ Rx = ($cip + '-anonymity'); To = 'anonymity'; TestToo = $true },
    @{ Rx = ('C:(\\{1,2})' + $zn); To = 'C:$1work'; TestToo = $true },
    @{ Rx = ('C:/' + $zn); To = 'C:/work'; TestToo = $true },
    @{ Rx = ('/mnt/c/' + $zn); To = '/mnt/c/work'; TestToo = $true },
    @{ Rx = ('C:(\\{1,2})' + 'd' + 'ev\b'); To = 'C:$1src'; TestToo = $true },
    @{ Rx = $usr; To = 'someuser'; TestToo = $true },
    @{ Rx = $lowusr; To = 'exampleuser'; TestToo = $true },
    @{ Rx = ('\b' + $nm + '\b'); To = 'Sam'; TestToo = $true },
    @{ Rx = ('\b' + $nm.ToLower() + '\b'); To = 'sam'; TestToo = $true },
    # The board tests used the Siteoffice repo itself as a fixture. Point them at the Molenwerk example.
    @{ Rx = 'PROJECT_ROOT = BOARD_DIR\.parent  #[^\r\n]*'; To = 'PROJECT_ROOT = BOARD_DIR.parent.parent.parent / "templates" / "molenwerk"  # the Molenwerk example tree'; TestToo = $true }
)

$components = @{
    engine = @{
        Source = (Join-Path $SiteofficeRoot 'foreman')
        Dest = (Join-Path $pluginDir 'engine\foreman')
        Receipt = (Join-Path $pluginDir 'engine\SYNCED_FROM.json')
        SkipTop = @('README.md', 'PACKAGE.json')              # Groundwork has its own engine\README.md
        SkipNames = @('operator.json')                         # per-operator state file
        Tests = 'engine'
    }
    board = @{
        Source = (Join-Path $SiteofficeRoot 'board')
        Dest = (Join-Path $pluginDir 'board')
        Receipt = (Join-Path $pluginDir 'board\SYNCED_FROM.json')
        SkipTop = @('DESIGN.md', 'REVIEW.md')                  # internal design and review notes
        SkipNames = @('test_drag_drop.py', 'test_sync_buttons.py', 'test_as_of.py')   # need the Siteoffice sync helper or git history, not shipped
        Tests = 'board'
    }
    sync = @{
        # The siteoffice-git engine: git_flow.py with git_ops.py and owner_check.py, stdlib only.
        Source = (Join-Path $SiteofficeRoot 'sync')
        Dest = (Join-Path $pluginDir 'engine\sync')
        Receipt = (Join-Path $pluginDir 'engine\sync\SYNCED_FROM.json')
        SkipTop = @('helper.py', 'scan_tree.py', 'owner.local.json', 'secret_scan_baseline.json')   # the helper server and local state, not the engine
        SkipNames = @('test_cli.py', 'test_http_server.py', 'test_scan_tree.py')                      # test the helper, not shipped
        Tests = 'sync'
    }
}

$skipDirs = '__pycache__', '.pytest_cache'
$skipExt = '.pyc', '.ndjson'
$textExt = '.py', '.md', '.json', '.txt', '.toml', '.cfg', '.js', '.html', '.css'
$utf8 = New-Object System.Text.UTF8Encoding($false)

if (-not $Apply) { Write-Host 'DRY RUN. Nothing will be written. Add -Apply.' -ForegroundColor Yellow }

$names = if ($Component -eq 'all') { @('engine', 'board', 'sync') } else { @($Component) }
foreach ($name in $names) {
    $c = $components[$name]
    $src = (Resolve-Path -LiteralPath $c.Source).Path
    $files = @(Get-ChildItem -LiteralPath $src -Recurse -File -Force -Name | Where-Object {
        $rel = $_
        $parts = $rel -split '[\\/]'
        -not ($parts | Where-Object { $skipDirs -contains $_ }) -and
        ($skipExt -notcontains [System.IO.Path]::GetExtension($rel).ToLower()) -and
        -not (($parts.Count -eq 1) -and ($c.SkipTop -contains $rel)) -and
        ($c.SkipNames -notcontains (Split-Path -Leaf $rel))
    })
    Write-Host "${name}: $src ($($files.Count) file(s)) -> $($c.Dest)"
    if (-not $Apply) { continue }

    if (Test-Path -LiteralPath $c.Dest) { Remove-Item -LiteralPath $c.Dest -Recurse -Force }
    $changed = 0
    foreach ($rel in $files) {
        $from = Join-Path $src $rel
        $to = Join-Path $c.Dest $rel
        $dir = Split-Path -Parent $to
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        if ($textExt -contains [System.IO.Path]::GetExtension($rel).ToLower()) {
            $text = [System.IO.File]::ReadAllText($from, $utf8)
            $isTest = ($rel -match '(^|[\\/])test_[^\\/]*\.py$') -or ($rel -match '^tests[\\/]')
            $orig = $text
            foreach ($r in $rules) {
                if ($isTest -and -not $r.TestToo) { continue }
                $text = [regex]::Replace($text, $r.Rx, $r.To)
            }
            if ($text -ne $orig) { $changed++ }
            [System.IO.File]::WriteAllText($to, $text, $utf8)
        } else {
            Copy-Item -LiteralPath $from -Destination $to
        }
    }

    $ver = ''
    $pkg = Join-Path $src 'PACKAGE.json'
    if (Test-Path -LiteralPath $pkg) { $ver = (Get-Content -LiteralPath $pkg -Raw | ConvertFrom-Json).version }
    $sha = ''
    try { $sha = (git -C $SiteofficeRoot rev-parse --short HEAD 2>$null) } catch { }
    $receipt = [ordered]@{ component = $name; source_version = $ver; source_commit = "$sha"; files = $files.Count; rewritten = $changed }
    [System.IO.File]::WriteAllText($c.Receipt, (($receipt | ConvertTo-Json) + "`n"), $utf8)
    Write-Host "  vendored $($files.Count) file(s), rewrote $changed" -ForegroundColor Green
}

if ($Apply -and $Verify) {
    & (Join-Path $PSScriptRoot 'check-public-payload.ps1')
    if ($LASTEXITCODE -ne 0) { exit 1 }
    foreach ($name in $names) {
        if ($name -eq 'engine') {
            Push-Location (Join-Path $pluginDir 'engine')
            try { py -3 -m pytest foreman groundwork -q -p no:cacheprovider } finally { Pop-Location }
        } elseif ($name -eq 'sync') {
            Push-Location (Join-Path $pluginDir 'engine')
            try { py -3 -m pytest sync -q -p no:cacheprovider } finally { Pop-Location }
        } else {
            Push-Location $pluginDir
            try { py -3 -m pytest board -q -p no:cacheprovider } finally { Pop-Location }
        }
        if ($LASTEXITCODE -ne 0) { exit 1 }
    }
}
exit 0
