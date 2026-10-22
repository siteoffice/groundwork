#Requires -Version 7.0
<#
.SYNOPSIS
  Siteoffice migrate, Mode 4: rename a unit in place. Dry run by default.

.DESCRIPTION
  A unit changes name: -Root is its current folder (must hold siteoffice.json). The script
  finds the parent unit (walking up from -Root until an ancestor's siteoffice.json 'name'
  matches the child's 'parent' field), updates the parent's children list entry (name and path),
  renames the one-pager at <parent>\01_projects\<oldName>.md to <newName>.md if one exists,
  renames the folder itself (git mv inside a git repository, Move-Item outside one), updates the
  unit's own siteoffice.json 'name' field, then rewrites every markdown pointer to the unit's old
  absolute path under -ScanRoot (default: walk up from -Root to the nearest holding-type
  manifest with no parent). History files (decisions.md, SESSION_LOG.md, anything under
  _archive, handovers, or 03_logs\_history) are excluded from the rewrite. A verification
  inventory expects zero old-path hits outside those excluded files.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeRename.ps1 -Root 'C:\estate\50_projects\old-name' -NewName 'new-name' -OutDir 'C:\estate\04_engineering\migration'
    .\Invoke-SiteofficeRename.ps1 -Root 'C:\estate\50_projects\old-name' -NewName 'new-name' -OutDir 'C:\estate\04_engineering\migration' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [Parameter(Mandatory = $true)][string]$NewName,
  [switch]$Apply,
  [string]$OutDir = '',
  [string]$ScanRoot = '',
  [string[]]$Extensions = @('.md'),
  [string[]]$ExcludeDirs = @('.git','.worktrees','node_modules','__pycache__','.venv','venv','dist','build'),
  [string[]]$HistoryFiles = @('decisions.md','SESSION_LOG.md'),
  [string[]]$HistoryDirs = @('_archive','handovers','03_logs\_history'),
  [int]$MaxFileMB = 5,
  [string]$RewriteScript = ''
)

$ErrorActionPreference = 'Stop'

function Test-DotExcluded { param([string]$Name) return ($Name.StartsWith('.') -and $Name -ne '.claude') }

function Test-ExcludedDir {
  param([string]$FullPath, [string]$Root)
  foreach ($d in $ExcludeDirs) { if ($FullPath -like "*\$d\*" -or $FullPath -like "*\$d") { return $true } }
  foreach ($h in $HistoryDirs) { if ($FullPath -like "*\$h\*" -or $FullPath -like "*\$h") { return $true } }
  $rel = $FullPath.Substring($Root.Length).TrimStart('\')
  foreach ($seg in ($rel -split '\\')) { if (Test-DotExcluded $seg) { return $true } }
  return $false
}

function Get-Manifest {
  param([string]$Path)
  $p = Join-Path $Path 'siteoffice.json'
  if (-not (Test-Path -LiteralPath $p)) { return $null }
  return (Get-Content -LiteralPath $p -Raw | ConvertFrom-Json)
}

function Find-EstateRoot {
  # walks up from $Path looking for the nearest ancestor whose manifest has type 'holding'
  # and an empty parent; falls back to the topmost ancestor that has any manifest at all.
  param([string]$Path)
  $cur = $Path
  $fallback = $null
  while ($cur -and $cur.Length -ge 3) {
    $m = Get-Manifest -Path $cur
    if ($m) {
      if (-not $fallback) { $fallback = $cur }
      if ($m.type -eq 'holding' -and [string]::IsNullOrWhiteSpace([string]$m.parent)) { return $cur }
    }
    $parent = Split-Path -Parent $cur
    if ($parent -eq $cur) { break }
    $cur = $parent
  }
  return $fallback
}

function Find-ParentUnit {
  # walks up from $Path (exclusive) looking for an ancestor whose manifest 'name' equals $ParentName
  param([string]$Path, [string]$ParentName)
  if ([string]::IsNullOrWhiteSpace($ParentName)) { return $null }
  $cur = Split-Path -Parent $Path
  while ($cur -and $cur.Length -ge 3) {
    $m = Get-Manifest -Path $cur
    if ($m -and $m.name -eq $ParentName) { return $cur }
    $parent = Split-Path -Parent $cur
    if ($parent -eq $cur) { break }
    $cur = $parent
  }
  return $null
}

function Find-GitRepoRoot {
  param([string]$Path, [string]$Boundary)
  $cur = Split-Path -Parent $Path
  while ($cur -and $cur.Length -ge 3) {
    if (Test-Path -LiteralPath (Join-Path $cur '.git')) { return $cur }
    if ($cur -eq $Boundary) { break }
    $parent = Split-Path -Parent $cur
    if ($parent -eq $cur) { break }
    $cur = $parent
  }
  return $null
}

function Get-Spellings {
  param([string]$Path)
  $drive = $Path.Substring(0,1).ToLower()
  $tail = $Path.Substring(3)
  $tailFwd = $tail -replace '\\','/'
  return @(
    $Path,
    ($Path -replace '\\','/'),
    ($Path -replace '\\','\\'),
    ("/mnt/{0}/{1}" -f $drive, $tailFwd),
    ("//{0}/{1}" -f $drive, $tailFwd)
  )
}

function Find-PathMentions {
  param([string]$OldPath, [string]$ScanRoot)
  $out = New-Object System.Collections.Generic.List[object]
  $spellings = Get-Spellings $OldPath
  $alts = ($spellings | ForEach-Object { [regex]::Escape($_) }) -join '|'
  # boundary: a safe punctuation/whitespace char, end of string, or a sentence-ending dot not
  # itself followed by more name characters (so ...\old-name.md is not a match of ...\old-name).
  $pattern = "(?i)(?:$alts)(?=[\\/`"'\s\)\]\>,;:]|\.(?![A-Za-z0-9])|$)"
  $extSet = @{}
  foreach ($e in $Extensions) { $extSet[$e.ToLower()] = $true }
  $maxBytes = $MaxFileMB * 1MB
  Get-ChildItem -LiteralPath $ScanRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { (-not (Test-ExcludedDir $_.DirectoryName $ScanRoot)) -and $extSet.ContainsKey($_.Extension.ToLower()) -and $_.Length -le $maxBytes } |
    ForEach-Object {
      $f = $_
      $isHistoryFile = $HistoryFiles -contains $f.Name
      $hits = $null
      $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
      try { $hits = Select-String -LiteralPath $f.FullName -Pattern $pattern -AllMatches -ErrorAction SilentlyContinue }
      finally { $ErrorActionPreference = $prev }
      if (-not $hits) { return }
      foreach ($h in $hits) {
        foreach ($m in $h.Matches) {
          $note = if ($isHistoryFile) { 'history file; excluded from rewrite' } else { '' }
          $ctx = $h.Line.Trim(); if ($ctx.Length -gt 200) { $ctx = $ctx.Substring(0,200) + '...' }
          $out.Add([pscustomobject]@{ Source = 'file'; Path = $f.FullName; Line = $h.LineNumber; Match = $m.Value; Context = $ctx; Note = $note })
        }
      }
    }
  return $out
}

try {
  $Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
  if ($Root.Length -lt 3 -or $Root.Substring(1,2) -ne ':\') { throw "Root must be a drive path like C:\estate\...\unit, got '$Root'" }
  $manifest = Get-Manifest -Path $Root
  if (-not $manifest) { throw "No siteoffice.json at $Root. Mode 4 renames a Siteoffice unit; adopt it first (Mode 5) if it is not one yet." }
  $oldName = Split-Path -Leaf $Root
  $parentDir = Split-Path -Parent $Root
  $newRoot = Join-Path $parentDir $NewName

  if ([string]::IsNullOrWhiteSpace($ScanRoot)) { $ScanRoot = Find-EstateRoot -Path $Root }
  if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = Join-Path $ScanRoot '10_holding\04_engineering\migration' }
  if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
  if ([string]::IsNullOrWhiteSpace($RewriteScript)) { $RewriteScript = Join-Path $PSScriptRoot 'Invoke-SiteofficeRewrite.ps1' }
  $stamp = Get-Date -Format 'yyyy-MM-dd'
  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }

  Write-Host "== Siteoffice rename, $mode, $stamp"
  Write-Host "Unit:      $Root  (name: $oldName, type: $($manifest.type), parent: $($manifest.parent))"
  Write-Host "New name:  $NewName"
  Write-Host "New path:  $newRoot"
  Write-Host "ScanRoot:  $ScanRoot"
  Write-Host "OutDir:    $OutDir"
  Write-Host ''

  if (Test-Path -LiteralPath $newRoot) { throw "Target already exists: $newRoot" }

  $parentPath = Find-ParentUnit -Path $Root -ParentName ([string]$manifest.parent)
  $childMatch = $null
  if ($parentPath) {
    Write-Host "Parent unit found: $parentPath"
    $pm = Get-Manifest -Path $parentPath
    if ($pm.children) {
      foreach ($c in $pm.children) { if ($c.name -eq $oldName -or ([string]$c.path) -like "*$oldName") { $childMatch = $c; break } }
    }
    if ($childMatch) {
      Write-Host ("  children[] entry: name='{0}' path='{1}'  ->  name='{2}' path='{3}'" -f $childMatch.name, $childMatch.path, $NewName, ([string]$childMatch.path -replace [regex]::Escape($oldName), $NewName))
    } else {
      Write-Host '  no matching children[] entry found (will be reported, not invented)'
    }
    $onePager = Join-Path $parentPath ("01_projects\{0}.md" -f $oldName)
    $onePagerExists = Test-Path -LiteralPath $onePager
    Write-Host ("  one-pager: {0}  {1}" -f $onePager, ($(if ($onePagerExists) { '(found, will rename)' } else { '(not found, skipped)' })))
  } else {
    Write-Host 'No parent unit found (this is the estate root, or its manifest names a parent that cannot be located). Parent-side updates skipped.'
  }

  Write-Host ''
  Write-Host 'Scanning for markdown pointers to the old path ...'
  $inv = Find-PathMentions -OldPath $Root -ScanRoot $ScanRoot
  $invCsv = Join-Path $OutDir "${stamp}_rename_${oldName}_inventory.csv"
  $inv | Export-Csv -LiteralPath $invCsv -NoTypeInformation -Encoding UTF8
  $histCount = @($inv | Where-Object { $_.Note -like 'history file*' }).Count
  Write-Host ("  file hits: {0} ({1} in history files, excluded from rewrite)" -f $inv.Count, $histCount)
  Write-Host "  inventory: $invCsv"

  # Windows refuses to rename a folder that is a process's working directory; check this process
  # and the shell that launched it before anything is touched.
  $cwds = @((Get-Location).ProviderPath, [Environment]::CurrentDirectory) | Select-Object -Unique
  $held = @($cwds | Where-Object { $_.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or $_.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase) })
  if ($held.Count -gt 0) {
    Write-Host ''
    Write-Host "WORKING DIRECTORY INSIDE THE UNIT THIS RUN RENAMES: $($held[0])"
    Write-Host '  cd to a folder outside the unit (for example C:\) and call this script by its full path.'
    if ($Apply) { throw 'REFUSED: the working directory is inside the unit that would be renamed; nothing was changed.' }
  }

  if (-not $Apply) {
    Write-Host ''
    Write-Host 'Dry run only. Nothing changed. Re-run with -Apply after reading the plan above.'
    exit 0
  }

  # --- apply: stage the rewrite script outside the tree ------------------------
  # $PSScriptRoot can be inside the unit this run renames, so run a copy from the temp folder.
  if (-not (Test-Path -LiteralPath $RewriteScript)) { throw "Rewrite script not found: $RewriteScript" }
  $stageDir = Join-Path ([IO.Path]::GetTempPath()) ('siteoffice-rn-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $stageDir | Out-Null
  Copy-Item -LiteralPath $RewriteScript -Destination $stageDir
  $RewriteScript = Join-Path $stageDir (Split-Path -Leaf $RewriteScript)

  # --- apply: parent children list ---------------------------------------------
  if ($parentPath -and $childMatch) {
    $pm2 = Get-Manifest -Path $parentPath
    foreach ($c in $pm2.children) {
      if ($c.name -eq $oldName -or ([string]$c.path) -like "*$oldName") {
        $c.name = $NewName
        $c.path = ([string]$c.path) -replace [regex]::Escape($oldName), $NewName
      }
    }
    ($pm2 | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath (Join-Path $parentPath 'siteoffice.json') -Encoding UTF8
    Write-Host "Updated children[] in $parentPath\siteoffice.json"
  }

  # --- apply: one-pager ----------------------------------------------------------
  $onePagerOld = $null; $onePagerNew = $null
  if ($parentPath) {
    $onePager = Join-Path $parentPath ("01_projects\{0}.md" -f $oldName)
    if (Test-Path -LiteralPath $onePager) {
      $newOnePager = Join-Path $parentPath ("01_projects\{0}.md" -f $NewName)
      $repo = Find-GitRepoRoot -Path $onePager -Boundary $ScanRoot
      if ($repo) {
        $relOld = $onePager.Substring($repo.Length + 1); $relNew = $newOnePager.Substring($repo.Length + 1)
        & git -C $repo mv -- $relOld $relNew
        if ($LASTEXITCODE -ne 0) { throw "git mv failed for one-pager $onePager" }
      } else {
        Move-Item -LiteralPath $onePager -Destination $newOnePager
      }
      Write-Host "Renamed one-pager: $onePager -> $newOnePager"
      $onePagerOld = $onePager; $onePagerNew = $newOnePager
    }
  }

  # --- apply: the unit folder itself ---------------------------------------------
  $repo = Find-GitRepoRoot -Path $Root -Boundary $ScanRoot
  if ($repo) {
    $relOld = $Root.Substring($repo.Length + 1); $relNew = $newRoot.Substring($repo.Length + 1)
    # no tracked files (gitignored or untracked): git mv fails with "source directory is empty"
    $tracked = & git -C $repo ls-files -- $relOld 2>$null
    if (-not $tracked) { Write-Host "  (no tracked files in $Root, plain move)"; $repo = $null }
  }
  if ($repo) {
    Write-Host "git mv   $Root -> $newRoot  (repo: $repo)"
    & git -C $repo mv -- $relOld $relNew
    if ($LASTEXITCODE -ne 0) { throw "git mv failed for $Root" }
  } else {
    Write-Host "move     $Root -> $newRoot"
    Move-Item -LiteralPath $Root -Destination $newRoot
  }

  # --- apply: the unit's own manifest --------------------------------------------
  $selfManifest = Get-Manifest -Path $newRoot
  $selfManifest.name = $NewName
  ($selfManifest | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath (Join-Path $newRoot 'siteoffice.json') -Encoding UTF8
  Write-Host "Updated name in $newRoot\siteoffice.json"

  # --- apply: markdown pointer rewrite --------------------------------------------
  Write-Host ''
  Write-Host 'Rewriting markdown pointers via Invoke-SiteofficeRewrite.ps1 ...'
  $rewriteRows = @($inv | Where-Object { $_.Note -notlike 'history file*' })
  $rewriteInvCsv = Join-Path $OutDir "${stamp}_rename_${oldName}_inventory_for_rewrite.csv"
  $rewriteRows | Export-Csv -LiteralPath $rewriteInvCsv -NoTypeInformation -Encoding UTF8
  $mapEntries = @([ordered]@{ old = $Root.Substring($ScanRoot.Length + 1); new = $newRoot.Substring($ScanRoot.Length + 1); type = 'rename'; git = 'none'; note = 'unit rename' })
  if ($onePagerOld) {
    $mapEntries += [ordered]@{ old = $onePagerOld.Substring($ScanRoot.Length + 1); new = $onePagerNew.Substring($ScanRoot.Length + 1); type = 'rename'; git = 'none'; note = 'one-pager rename' }
  }
  $synthMap = [ordered]@{
    schemaVersion = 1; oldRoot = $ScanRoot; newRoot = $ScanRoot; holdingIsCompany = $false
    staysSubfolders = @(); rollbackDays = 0
    entries = $mapEntries
  }
  $synthMapPath = Join-Path $OutDir "${stamp}_rename_${oldName}_synthetic_map.json"
  ($synthMap | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $synthMapPath -Encoding UTF8
  $rewriteReport = Join-Path $OutDir "${stamp}_rename_${oldName}_rewrite_report.csv"
  & pwsh -NoProfile -File $RewriteScript -Inventory $rewriteInvCsv -Map $synthMapPath -Apply -ReportCsv $rewriteReport -NoRelativeForms
  if ($LASTEXITCODE -ne 0) { throw "Invoke-SiteofficeRewrite.ps1 exited $LASTEXITCODE" }
  Write-Host "  rewrite report: $rewriteReport"

  # --- verification ----------------------------------------------------------------
  Write-Host ''
  Write-Host 'Verification: re-scanning for the old path ...'
  $verify = Find-PathMentions -OldPath $Root -ScanRoot $ScanRoot
  $verifyCsv = Join-Path $OutDir "${stamp}_rename_${oldName}_verify.csv"
  $verify | Export-Csv -LiteralPath $verifyCsv -NoTypeInformation -Encoding UTF8
  $remaining = @($verify | Where-Object { $_.Note -notlike 'history file*' })
  Write-Host ("  remaining hits (excluding history files): {0}" -f $remaining.Count)
  Write-Host "  verify CSV: $verifyCsv"
  if (-not $parentPath) { Write-Host '  note: no parent unit was found, so children[] and the one-pager were not touched.' }
  elseif (-not $childMatch) { Write-Host '  note: no children[] entry matched the old name; add one by hand.' }

  Write-Host ''
  Write-Host 'Done.'
  exit 0
}
catch {
  Write-Host ("RENAME FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
finally {
  if ($stageDir -and (Test-Path -LiteralPath $stageDir)) { Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue }
}
