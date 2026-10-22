#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice cleanup, orphan pass. Read-only. Lists markdown and html documents under a unit
  that no other file in the tree refers to by name, with their age, so the user can decide what
  moves to _history or _archive.

.DESCRIPTION
  A document nobody links to and nobody has touched for -StaleDays is a candidate. Not a verdict:
  the user decides per row. The scan matches the bare filename (case-insensitive) in every text
  file under the root, excluding .git, caches, _archive and _history themselves. Instruction
  files, STATUS, DEADLINES, SESSION_LOG, README and roadmap files are never listed: they are
  entry points, not orphans.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeOrphans.ps1 -Root 'C:\siteoffice\acme\10_holding' -StaleDays 60 -OutCsv 'C:\siteoffice\acme\10_holding\04_assets\2026-01-31_orphans.csv'
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [int]$StaleDays = 60,
  [string]$OutCsv = '',
  [string[]]$DocExtensions = @('.md','.html','.txt','.json'),
  [string[]]$ExcludeDirs = @('.git','node_modules','__pycache__','.venv','venv','dist','build','_archive','_history','graphify-out','.worktrees'),
  [string[]]$EntryPoints = @('AGENTS.md','CLAUDE.md','STATUS.md','DEADLINES.md','SESSION_LOG.md','README.md','roadmap.md','decisions.md','portfolio.md','siteoffice.json','index.html','index.json','hub_inbox.md')
)

$ErrorActionPreference = 'Stop'
try {
  $Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
  if ([string]::IsNullOrWhiteSpace($OutCsv)) { $OutCsv = Join-Path $env:TEMP ("siteoffice_orphans_{0}.csv" -f (Get-Date -Format 'yyyy-MM-dd')) }
  $now = Get-Date

  function Test-Excluded { param([string]$p) foreach ($d in $ExcludeDirs) { if ($p -like "*\$d\*") { return $true } }; return $false }

  Write-Host "Reading text files under $Root ..."
  $all = Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { -not (Test-Excluded $_.FullName) -and $_.Length -le 5MB }
  $textExt = @('.md','.html','.txt','.json','.yml','.yaml','.toml','.ps1','.py','.js','.ts','.tsv','.csv','.ini','.cfg')
  $corpus = New-Object System.Text.StringBuilder
  foreach ($f in $all) {
    if ($textExt -contains $f.Extension.ToLower()) {
      try { [void]$corpus.Append([IO.File]::ReadAllText($f.FullName).ToLowerInvariant()); [void]$corpus.Append("`n") } catch { }
    }
  }
  $text = $corpus.ToString()
  Write-Host ("  {0} files, {1:N0} characters of text" -f $all.Count, $text.Length)

  $rows = New-Object System.Collections.Generic.List[object]
  $docs = $all | Where-Object { $DocExtensions -contains $_.Extension.ToLower() -and ($EntryPoints -notcontains $_.Name) }
  foreach ($d in $docs) {
    $name = $d.Name.ToLowerInvariant()
    # count occurrences of the bare filename; the file's own path contributes none because we search content, not names
    $idx = 0; $refs = 0
    while (($idx = $text.IndexOf($name, $idx, [StringComparison]::Ordinal)) -ge 0) { $refs++; $idx += $name.Length; if ($refs -gt 50) { break } }
    # a file that mentions its own name (frontmatter title) counts once; subtract if it does
    try { if ([IO.File]::ReadAllText($d.FullName).ToLowerInvariant().Contains($name)) { $refs-- } } catch { }
    $days = [int]($now - $d.LastWriteTime).TotalDays
    if ($refs -le 0) {
      $rows.Add([pscustomobject]@{
        Path = $d.FullName; KB = [math]::Round($d.Length / 1KB, 1); LastWrite = $d.LastWriteTime.ToString('yyyy-MM-dd'); DaysIdle = $days
        Referenced = 0; Stale = $(if ($days -ge $StaleDays) { 'yes' } else { '' }); DatePrefixed = $(if ($d.Name -match '^\d{4}-\d{2}-\d{2}') { 'yes' } else { '' }); Decision = ''
      })
    }
  }
  $rows | Sort-Object DaysIdle -Descending | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8
  Write-Host ''
  Write-Host ("{0} documents scanned, {1} unreferenced, {2} of those idle {3}+ days" -f @($docs).Count, $rows.Count, @($rows | Where-Object Stale -eq 'yes').Count, $StaleDays)
  $rows | Sort-Object DaysIdle -Descending | Select-Object -First 25 | ForEach-Object { Write-Host ("  {0,4} d  {1,7} KB  {2}" -f $_.DaysIdle, $_.KB, $_.Path.Substring($Root.Length + 1)) }
  Write-Host "CSV: $OutCsv   (fill Decision: keep, history, archive, delete, ask)"
  exit 0
}
catch {
  Write-Host ("ORPHANS FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
