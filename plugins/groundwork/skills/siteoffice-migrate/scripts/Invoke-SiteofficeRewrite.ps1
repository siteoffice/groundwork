#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, path rewrite. After a restructure or relocate, rewrites every hard-coded
  old-root path inside the COPIED files at the new root, following the migration map. Dry run
  by default. Reads the inventory CSV to know which files to touch.

.DESCRIPTION
  For each file row in the inventory that is not skipped (stays, generated, data, archive, .git
  internals), the script finds the file's new location via the map, reads it, and replaces each
  occurrence of the old root (in the spelling found: backslash, forward slash, JSON-escaped,
  WSL /mnt, git-bash //) by the new root in the same spelling, remapping the first path segments
  when a map entry moved that folder elsewhere (e.g. hub\packs -> _packs). Files in the OLD root
  are never touched. Encoding (BOM or not) and line endings are preserved.

  v2 (2026-09-09), after the first real estate: (1) a path is now recognised when a name
  character does not follow it, so `C:\old-root\hub` in backticks, before a `*` or a `)` is
  remapped as a whole; v1 stopped at the root and produced <newRoot>\<oldName> paths that do not
  exist. (2) Entries of type archive and data ARE remapped in text (their folders moved); only
  the files inside them are left unrewritten. Estates rewritten with v1 are repaired with
  Invoke-SiteofficeEntryFixup.ps1.

  v3 (2026-10-01): also rewrites what an absolute-path scan never sees, the same forms Mode 6
  (rename-to-rule) handles: relative forms (hub\packs\x, ..\apps\web\x, siteoffice\docs\x, where the
  moved folder may be any segment) and the forward-slash keys of a .tsv ledger. A candidate is read in
  the OLD tree and kept only if a moved folder is on its path and the next segment exists on disk next
  to the file or one of its ancestors up to the old root; the new form is recomputed from where file
  and target land. Every proposal goes to <ReportCsv>_relative.csv, written on a dry run too: read it
  before -Apply. History files and history folders are never touched, a file that is not valid UTF-8 is
  skipped and listed, a form that cannot keep its shape (the target stays while the file moves) is
  listed as 'review' and left alone. The logic lives in SiteofficeRelativeForms.ps1 next to this script,
  shared with Mode 6. -NoRelativeForms switches this off (Mode 4 and Mode 6 pass it: they rewrite in
  place and have their own relative pass or none).

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeRewrite.ps1 -Inventory 'C:\old-root\hub\04_engineering\migration\2026-01-31_path_inventory.csv' -Map 'C:\old-root\hub\04_engineering\migration\2026-01-31_migration_map.json'
    .\Invoke-SiteofficeRewrite.ps1 -Inventory '<same>' -Map '<same>' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Inventory,
  [Parameter(Mandatory = $true)][string]$Map,
  [switch]$Apply,
  [string[]]$SkipNotesLike = @('stays subtree*','generated*'),
  [string[]]$SkipTypes = @('stays','delete','generated','data','archive'),
  [string[]]$NoRemapTypes = @('stays','delete'),
  [string[]]$SkipPathsLike = @('*\.git\*','*\node_modules\*'),
  [string[]]$AlsoRewrite = @(),
  [string]$ReportCsv = '',
  [switch]$NoRelativeForms,
  [string[]]$Extensions = @('.md','.json','.ps1','.py','.html','.toml','.yml','.yaml','.tsv','.bat','.cmd'),
  [string[]]$ExcludeDirs = @('.git','.worktrees','node_modules','__pycache__','.venv','venv','dist','build'),
  [string[]]$HistoryFiles = @('decisions.md','SESSION_LOG.md'),
  [string[]]$HistoryDirs = @('_archive','handovers','03_logs\_history'),
  [int]$MaxFileMB = 5
)
# -AlsoRewrite: wildcard paths OUTSIDE the old root to rewrite IN PLACE (tool configs such as
#   *\.claude\settings.json, *\.claude\commands\*.md). Off by default; caches are never a good idea here.

# -SkipTypes: entry types whose FILES are not rewritten (frozen history, data). Their paths in
#   other files are still remapped unless the type is also in -NoRemapTypes.

$ErrorActionPreference = 'Stop'
# a path ends where a name character does not follow: letters, digits, _ - . ~ are name characters
$nameChar = '[A-Za-z0-9_\-\.~]'

function Get-Spellings {
  param([string]$Root)
  $drive = $Root.Substring(0,1).ToLower()
  $tail = $Root.Substring(3)
  $tailFwd = $tail -replace '\\','/'
  return [ordered]@{
    bs   = $Root
    fs   = ($Root -replace '\\','/')
    json = ($Root -replace '\\','\\')
    wsl  = ("/mnt/{0}/{1}" -f $drive, $tailFwd)
    gb   = ("//{0}/{1}" -f $drive, $tailFwd)
  }
}

function Convert-RelToSpelling {
  # a relative path with backslashes, rendered in the separator style of one spelling
  param([string]$Rel, [string]$Style)
  switch ($Style) {
    'bs'   { return $Rel }
    'json' { return ($Rel -replace '\\','\\') }
    default { return ($Rel -replace '\\','/') }
  }
}

try {
  $m = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Map).Path -Raw | ConvertFrom-Json
  $oldRoot = $m.oldRoot.TrimEnd('\'); $newRoot = $m.newRoot.TrimEnd('\')
  $oldSp = Get-Spellings $oldRoot; $newSp = Get-Spellings $newRoot
  $stays = @($m.staysSubfolders)

  # entries as relative old -> relative new, longest old first
  $remaps = @()
  foreach ($e in $m.entries) {
    if ($e.old -eq '' -or ($NoRemapTypes -contains $e.type) -or $e.new -eq '') { continue }
    $relOld = $e.old
    if ([IO.Path]::IsPathRooted($relOld)) { $relOld = $relOld.Substring($oldRoot.Length).TrimStart('\') }
    $remaps += [pscustomobject]@{ old = $relOld.TrimEnd('\'); new = $e.new.TrimEnd('\'); type = $e.type; depth = ($relOld.Split('\').Count) }
  }
  $remaps = $remaps | Sort-Object depth -Descending
  $skipRel = @($m.entries | Where-Object { $SkipTypes -contains $_.type -and $_.old -ne '' } | ForEach-Object {
    $r = $_.old; if ([IO.Path]::IsPathRooted($r)) { $r = $r.Substring($oldRoot.Length).TrimStart('\') }; $r.TrimEnd('\') })

  function Resolve-NewRel {
    # maps a relative old path (backslashes) to its relative new path, or $null if it stays
    param([string]$Rel)
    foreach ($s in $stays) { if ($Rel -eq $s -or $Rel.StartsWith($s + '\', 'OrdinalIgnoreCase')) { return $null } }
    foreach ($r in $remaps) {
      if ($Rel -eq $r.old) { return $r.new }
      if ($Rel.StartsWith($r.old + '\', 'OrdinalIgnoreCase')) { return ($r.new + $Rel.Substring($r.old.Length)) }
    }
    return $Rel   # no entry: same relative place under the new root
  }

  function Test-SkipRel {
    param([string]$Rel)
    foreach ($s in $skipRel) { if ($Rel -eq $s -or $Rel.StartsWith($s + '\', 'OrdinalIgnoreCase')) { return $true } }
    return $false
  }

  # --- which files ---------------------------------------------------------------
  $rows = Import-Csv -LiteralPath (Resolve-Path -LiteralPath $Inventory).Path
  $files = @{}
  foreach ($row in $rows) {
    if ($row.Source -ne 'file') { continue }
    $skip = $false
    foreach ($n in $SkipNotesLike) { if ($row.Note -like $n) { $skip = $true; break } }
    foreach ($p in $SkipPathsLike) { if ($row.Path -like $p) { $skip = $true; break } }
    if ($skip) { continue }
    if (-not $row.Path.StartsWith($oldRoot + '\', 'OrdinalIgnoreCase')) {
      # per-user tool folders: report only, unless the user opted a pattern in
      foreach ($pat in $AlsoRewrite) { if ($row.Path -like $pat) { $files[$row.Path] = $row.Path; break } }
      continue
    }
    $rel = $row.Path.Substring($oldRoot.Length + 1)
    if (Test-SkipRel $rel) { continue }
    $newRel = Resolve-NewRel $rel
    if ($null -eq $newRel) { continue }
    $files[(Join-Path $newRoot $newRel)] = $row.Path
  }
  $outside = @($rows | Where-Object { $_.Source -eq 'file' -and -not $_.Path.StartsWith($oldRoot + '\', 'OrdinalIgnoreCase') -and -not $files.ContainsKey($_.Path) } | Select-Object -ExpandProperty Path -Unique)

  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  Write-Host "== Siteoffice rewrite, $mode"
  Write-Host "old: $oldRoot   new: $newRoot   files to rewrite: $($files.Count)"
  Write-Host ''
  if ([string]::IsNullOrWhiteSpace($ReportCsv)) { $ReportCsv = Join-Path $env:TEMP ("siteoffice_rewrite_{0}_{1}.csv" -f (Get-Date -Format 'yyyy-MM-dd'), ($mode -replace ' ', '').ToLower()) }

  # --- relative forms and ledger keys (read in the old tree, applied to the copies, before the text rewrite) ---
  $report = New-Object System.Collections.Generic.List[object]
  if (-not $NoRelativeForms) {
    . (Join-Path $PSScriptRoot 'SiteofficeRelativeForms.ps1')
    $relRows = @()
    foreach ($r in $remaps) {
      $abs = Join-Path $oldRoot $r.old
      if ($r.old -ne $r.new -and (Test-Path -LiteralPath $abs -PathType Container)) { $relRows += [pscustomobject]@{ Old = $abs; New = (Join-Path $newRoot $r.new) } }
    }
    $mapPathSb = {
      param([string]$p)
      if ($p.Equals($oldRoot, [StringComparison]::OrdinalIgnoreCase)) { return $newRoot }
      if (-not $p.StartsWith($oldRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { return $null }
      $n = Resolve-NewRel $p.Substring($oldRoot.Length + 1)
      if ($null -eq $n) { return $null }
      return (Join-Path $newRoot $n)
    }
    $extSet = @{}; foreach ($e in $Extensions) { $extSet[$e.ToLower()] = $true }
    $relFiles = @(Get-ChildItem -LiteralPath $oldRoot -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
      $ok = $extSet.ContainsKey($_.Extension.ToLower()) -and $_.Length -le ($MaxFileMB * 1MB)
      if ($ok) {
        if (Test-SkipRel $_.FullName.Substring($oldRoot.Length + 1)) { $ok = $false }
        foreach ($pat in $SkipPathsLike) { if ($_.FullName -like $pat) { $ok = $false } }
        foreach ($d in ($ExcludeDirs + $HistoryDirs)) { if ($_.DirectoryName -like "*\$d" -or $_.DirectoryName -like "*\$d\*") { $ok = $false } }
        foreach ($seg in ($_.DirectoryName.Substring($oldRoot.Length).TrimStart('\') -split '\\')) { if ($seg.StartsWith('.') -and $seg -ne '.claude') { $ok = $false } }
      }
      $ok })
    $skippedNonUtf8 = New-Object System.Collections.Generic.List[string]
    $relFound = @(Find-RelativeMentions -File $relFiles -Root $oldRoot -Rows $relRows -MapPath $mapPathSb -HistoryFiles $HistoryFiles -SkippedNonUtf8 $skippedNonUtf8)
    $relBase = $ReportCsv -replace '\.csv$', ''
    $relCsv = $relBase + '_relative.csv'
    $relFound | Export-Csv -LiteralPath $relCsv -NoTypeInformation -Encoding UTF8
    $relHist = @($relFound | Where-Object { $_.Note -like 'history file*' }).Count
    $relReview = @($relFound | Where-Object { $_.Note -like 'review*' }).Count
    $relApply = @($relFound | Where-Object { $_.Note -eq '' })
    Write-Host ("relative forms and ledger keys: {0} to rewrite, {1} in history files (left alone), {2} to review by hand" -f $relApply.Count, $relHist, $relReview)
    Write-Host "  read before -Apply: $relCsv"
    foreach ($nf in $skippedNonUtf8) {
      Write-Host "  not UTF-8, never rewritten, edit by hand: $nf"
      $report.Add([pscustomobject]@{ File = $nf; Replacements = 0; Status = 'skipped: not valid UTF-8 (relative forms), edit by hand' })
    }
    if ($Apply -and $relApply.Count -gt 0) {
      $relReport = Invoke-RelativeRewrite -RelRows $relApply -MapPath $mapPathSb
      $relReport | Export-Csv -LiteralPath ($relBase + '_relative_report.csv') -NoTypeInformation -Encoding UTF8
      Write-Host ("  rewritten: {0} of {1}; report: {2}" -f @($relReport | Where-Object { $_.Status -eq 'rewritten' }).Count, $relReport.Count, ($relBase + '_relative_report.csv'))
    }
    Write-Host ''
  }

  # --- the regex: any spelling of the old root plus the path that follows -------
  $alts = ($oldSp.Values | ForEach-Object { [regex]::Escape($_) }) -join '|'
  $rx = New-Object System.Text.RegularExpressions.Regex ("(?i)(?<root>$alts)(?<rest>(?:[\\/]+$nameChar+)*)(?!$nameChar)")

  $totalRepl = 0; $changed = 0; $missing = 0
  foreach ($newPath in ($files.Keys | Sort-Object)) {
    if (-not (Test-Path -LiteralPath $newPath)) { $missing++; $report.Add([pscustomobject]@{ File = $newPath; Replacements = 0; Status = 'missing at new root (copy first)' }); continue }
    $bytes = [IO.File]::ReadAllBytes($newPath)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    # Strict decode: a Windows-1252 .bat would otherwise come back with U+FFFD in place of its accents.
    try { $text = (New-Object Text.UTF8Encoding($false, $true)).GetString($bytes) }
    catch { $report.Add([pscustomobject]@{ File = $newPath; Replacements = 0; Status = 'skipped: not valid UTF-8, edit by hand' }); Write-Host "  not UTF-8, left alone: $newPath"; continue }
    if ($hasBom) { $text = $text.TrimStart([char]0xFEFF) }
    $script:count = 0
    $newText = $rx.Replace($text, {
      param($match)
      $rootFound = $match.Groups['root'].Value
      $rest = $match.Groups['rest'].Value
      $style = 'bs'
      foreach ($k in $oldSp.Keys) { if ($oldSp[$k] -ieq $rootFound) { $style = $k; break } }
      $relBs = ($rest -replace '/', '\') -replace '\\\\', '\'   # normalise to single backslashes
      $relBs = $relBs.TrimStart('\')
      # a sentence-ending dot is not part of the path
      $tail = ''
      while ($relBs.EndsWith('.')) { $relBs = $relBs.Substring(0, $relBs.Length - 1); $tail += '.' }
      $newRel = Resolve-NewRel $relBs
      if ($null -eq $newRel) { return $match.Value }   # stays subtree
      $script:count++
      if ($newRel -eq '') { return $newSp[$style] + $tail }
      $sep = if ($style -eq 'bs') { '\' } elseif ($style -eq 'json') { '\\' } else { '/' }
      return $newSp[$style] + $sep + (Convert-RelToSpelling $newRel $style) + $tail
    })
    $count = $script:count
    if ($count -gt 0) {
      $changed++; $totalRepl += $count
      $ex = $rx.Match($text)
      Write-Host ("{0,5}  {1}" -f $count, $newPath)
      if ($ex.Success) { Write-Host ("       e.g. {0}" -f $ex.Value) }
      if ($Apply) {
        $enc = New-Object System.Text.UTF8Encoding($hasBom)
        [IO.File]::WriteAllText($newPath, $newText, $enc)
      }
      $report.Add([pscustomobject]@{ File = $newPath; Replacements = $count; Status = $(if ($Apply) { 'rewritten' } else { 'would rewrite' }) })
    } else {
      $report.Add([pscustomobject]@{ File = $newPath; Replacements = 0; Status = 'no match (already clean or inside skipped subtree)' })
    }
  }

  Write-Host ''
  Write-Host ("files changed: {0}, replacements: {1}, files missing at new root: {2}" -f $changed, $totalRepl, $missing)
  if ($outside.Count -gt 0) {
    Write-Host ''
    Write-Host ("{0} file(s) OUTSIDE the old root also hold the path (tool folders). Not touched; edit by hand, re-run their setup, or opt a pattern in with -AlsoRewrite:" -f $outside.Count)
    $outside | Select-Object -First 30 | ForEach-Object { Write-Host "  $_" }
  }
  $report | Export-Csv -LiteralPath $ReportCsv -NoTypeInformation -Encoding UTF8
  Write-Host "report: $ReportCsv"
  if (-not $Apply) { Write-Host 'Dry run only. Nothing changed. Re-run with -Apply after reading the report.' }
  exit 0
}
catch {
  Write-Host ("REWRITE FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
