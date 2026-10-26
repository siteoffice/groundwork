<#
.SYNOPSIS
  Payload gate for the public groundwork repo. Exits 1 on any hit, 0 when clean.

.DESCRIPTION
  G2: no private names or private paths in any text file.
  G3: no pack file, ADR, _drafts folder or dump in a public path.
  Skips a .git folder or .git pointer file and this script. Binary files are skipped by extension (the repo carries none;
  screenshots are checked by name only).

.EXAMPLE
  PowerShell, from the repo root (C:\ZND\50_projects\groundwork):
    pwsh -NoProfile -File .\scripts\check-public-payload.ps1
  Another folder (for example a planted-hit test):
    pwsh -NoProfile -File .\scripts\check-public-payload.ps1 -Root C:\some\folder
#>
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path -LiteralPath $Root).Path
$bs = [char]92

# G2. Patterns are built from fragments so this file does not trip its own gate.
$caseInsensitive = @(
    ('\bTal' + 'on\b'),
    ('\bMarti' + 'jn\b'),
    ('\bBa' + 'rt\b'),
    ('\bJero' + 'en\b'),
    ('A-INSIGH' + 'TS'),
    ('Valo' + 'na'),
    ('wilcode' + 'tree'),
    ('C:[/' + $bs + $bs + ']+ZN' + 'D'),
    ('C:[/' + $bs + $bs + ']+d' + 'ev\b'),
    ('C:[/' + $bs + $bs + ']+zeronons' + 'ensedev')
)
# The folder name 10_holding is allowed: it is the standard Siteoffice layout name, not a private
# name (Wilco, 2026-10-21, overrides the build handoff list).
$caseSensitive = @(('\bCIPH' + 'ER\b'))

# G3. Path rules, matched against the path relative to the root.
$sep = '[/' + $bs + $bs + ']'
$pathRules = @(
    @{ Name = 'pack file or folder'; Rx = ('(^|' + $sep + ')_packs?(' + $sep + '|$)|\.pack$') },
    @{ Name = 'ADR';                 Rx = ('(^|' + $sep + ')adr' + $sep + '|_ADR_|(^|' + $sep + ')ADR-') },
    @{ Name = '_drafts folder';      Rx = ('(^|' + $sep + ')_drafts(' + $sep + '|$)') },
    @{ Name = 'dump';                Rx = 'dump' }
)

$skipExt = '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.pyc'
$hits = New-Object System.Collections.Generic.List[string]
$selfRel = 'scripts' + $bs + 'check-public-payload.ps1'

$files = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force -Name |
    Where-Object { $_ -notmatch ('(^|' + $sep + ')(\.git|__pycache__|\.pytest_cache|_board|\.playwright-mcp|\.groundwork)' + $sep) -and $_ -notmatch ('(^|' + $sep + ')\.git$') -and $_ -ne $selfRel })

foreach ($rel in $files) {
    $full = Join-Path $Root $rel

    foreach ($r in $pathRules) {
        if ($rel -match $r.Rx) { $hits.Add("G3 $($r.Name): $rel") }
    }

    if ($skipExt -contains [System.IO.Path]::GetExtension($rel).ToLower()) { continue }
    $n = 0
    foreach ($line in [System.IO.File]::ReadLines($full)) {
        $n++
        foreach ($p in $caseInsensitive) {
            if ($line -imatch $p) { $hits.Add("G2 ${rel}:${n}: matches /$p/") }
        }
        foreach ($p in $caseSensitive) {
            if ($line -cmatch $p) { $hits.Add("G2 ${rel}:${n}: matches /$p/") }
        }
    }
}

if ($hits.Count -gt 0) {
    $hits | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    Write-Host "FAIL: $($hits.Count) hit(s). The payload is not public-clean." -ForegroundColor Red
    exit 1
}
Write-Host "OK: payload clean ($($files.Count) file(s) checked)." -ForegroundColor Green
exit 0
