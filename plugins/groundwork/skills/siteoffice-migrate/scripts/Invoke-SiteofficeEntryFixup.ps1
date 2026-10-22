#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, repair pass for an estate rewritten with Invoke-SiteofficeRewrite.ps1 v1.
  Dry run by default.

.DESCRIPTION
  v1 replaced the root but applied the per-entry map only when the old folder name was followed
  by a path separator, and never for archive entries. The result is paths such as
  <newRoot>\<oldName> that do not exist. This pass maps <newRoot>\<oldName> to <newRoot>\<newPath>
  for every map entry, and <newRoot>\<stays> back to <oldRoot>\<stays>. It runs over the NEW root
  (or the paths you name) in three spellings: backslash, JSON double backslash, forward slash.
  Encoding (BOM or not) is preserved. Not needed for estates rewritten with v2.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeEntryFixup.ps1 -Map 'C:\siteoffice\acme\10_holding\04_engineering\migration\2026-01-31_migration_map.json' -ReportCsv 'C:\siteoffice\acme\10_holding\04_engineering\migration\plans\fixup_dryrun.csv'
    .\Invoke-SiteofficeEntryFixup.ps1 -Map '<same>' -ReportCsv '<...>\fixup_apply.csv' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Map,
  [string[]]$Paths = @(),
  [string]$ReportCsv = '',
  [switch]$Apply,
  [string[]]$ExcludeDirs = @('.git','.worktrees','node_modules','.venv','venv','dist','build','.next','__pycache__','.pytest_cache','.ruff_cache','.mypy_cache','coverage','graphify-out'),
  [string[]]$ExcludePathPrefixes = @()
)
# -Paths: folders or files to fix; default is the map's newRoot.
# -ExcludePathPrefixes: full-path prefixes that keep their old-path text on purpose (migration
#   plans, this script's own folder). Case-insensitive.

$ErrorActionPreference = 'Stop'
try {
  $m = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Map).Path -Raw -Encoding UTF8 | ConvertFrom-Json
  $oldRoot = $m.oldRoot.TrimEnd('\'); $newRoot = $m.newRoot.TrimEnd('\')
  if ($Paths.Count -eq 0) { $Paths = @($newRoot) }

  # rules: old name -> new relative path, or back to the old root for stays. Longest old name first.
  $rules = @()
  foreach ($e in $m.entries) {
    if ([string]::IsNullOrEmpty($e.old)) { continue }
    $relOld = $e.old
    if ([IO.Path]::IsPathRooted($relOld)) { $relOld = $relOld.Substring($oldRoot.Length).TrimStart('\') }
    if ($e.type -eq 'stays') { $rules += [pscustomobject]@{ old = $relOld; root = $oldRoot; new = $relOld }; continue }
    if ([string]::IsNullOrEmpty($e.new) -or $e.new -eq $relOld) { continue }
    $rules += [pscustomobject]@{ old = $relOld; root = $newRoot; new = $e.new }
  }
  foreach ($s in @($m.staysSubfolders)) {
    if ($s -and -not ($rules | Where-Object { $_.old -eq $s })) { $rules += [pscustomobject]@{ old = $s; root = $oldRoot; new = $s } }
  }
  $rules = $rules | Sort-Object { $_.old.Length } -Descending

  $seps = @('\', '\\', '/')
  $compiled = foreach ($rule in $rules) {
    foreach ($sep in $seps) {
      $oldSpelled = ($newRoot + '\' + $rule.old) -replace '\\', $sep
      $newSpelled = ($rule.root + '\' + $rule.new) -replace '\\', $sep
      [pscustomobject]@{
        rx  = New-Object System.Text.RegularExpressions.Regex ('(?i)' + [regex]::Escape($oldSpelled) + '(?![A-Za-z0-9_\-])')
        rep = $newSpelled.Replace('$', '$$')
      }
    }
  }

  function Test-TextFile([string] $p) {
    $fs = [IO.File]::OpenRead($p)
    try { $buf = New-Object byte[] 8192; $n = $fs.Read($buf, 0, 8192) } finally { $fs.Dispose() }
    for ($i = 0; $i -lt $n; $i++) { if ($buf[$i] -eq 0) { return $false } }
    return $true
  }

  $files = foreach ($p in $Paths) {
    if (Test-Path -LiteralPath $p -PathType Leaf) { Get-Item -LiteralPath $p; continue }
    Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
      $rel = $_.FullName.Substring($p.Length); $full = $_.FullName
      -not ($rel.Split('\') | Where-Object { $ExcludeDirs -contains $_ }) -and $_.Length -lt 5MB -and
      -not ($ExcludePathPrefixes | Where-Object { $full.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) })
    }
  }

  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  Write-Host "== Siteoffice entry fixup, $mode"
  Write-Host "root: $newRoot   rules: $($rules.Count)   candidate files: $(@($files).Count)"
  $report = New-Object System.Collections.Generic.List[object]
  $utf8Bom = [byte[]](0xEF,0xBB,0xBF)
  $rootRx = New-Object System.Text.RegularExpressions.Regex ('(?i)' + [regex]::Escape($newRoot) + '[\\/]+[^\s`"''<>)]{1,60}')
  foreach ($f in $files) {
    if (-not (Test-TextFile $f.FullName)) { continue }
    $bytes = [IO.File]::ReadAllBytes($f.FullName)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $off = if ($hasBom) { 3 } else { 0 }
    $text = [Text.Encoding]::UTF8.GetString($bytes, $off, $bytes.Length - $off)
    $orig = $text; $n = 0
    foreach ($c in $compiled) { $hits = $c.rx.Matches($text).Count; if ($hits) { $n += $hits; $text = $c.rx.Replace($text, $c.rep) } }
    if ($n -eq 0) { continue }
    Write-Host ("{0,5}  {1}" -f $n, $f.FullName)
    $sample = $rootRx.Match($orig).Value
    if ($sample) { Write-Host "       e.g. $sample" }
    if ($Apply) {
      $body = [Text.Encoding]::UTF8.GetBytes($text)
      if ($hasBom) { $body = $utf8Bom + $body }
      [IO.File]::WriteAllBytes($f.FullName, $body)
    }
    $report.Add([pscustomobject]@{ File = $f.FullName; Replacements = $n; Status = $(if ($Apply) { 'rewritten' } else { 'would rewrite' }) })
  }
  $total = 0; if ($report.Count -gt 0) { $total = ($report | Measure-Object Replacements -Sum).Sum }
  Write-Host ''
  Write-Host "files: $($report.Count), replacements: $total"
  if ($ReportCsv) { $report | Export-Csv -LiteralPath $ReportCsv -NoTypeInformation -Encoding UTF8; Write-Host "report: $ReportCsv" }
  if (-not $Apply) { Write-Host 'Dry run only. Nothing changed. Re-run with -Apply after reading the report.' }
  exit 0
}
catch {
  Write-Host ("FIXUP FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
