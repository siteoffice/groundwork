#Requires -Version 7.0
<#
.SYNOPSIS
  Siteoffice migrate, Mode 6: rename-to-rule. Brings a tree's root-level folders in line with
  the folder-naming rule (numbered, underscore, or dotfolder; nothing else at a root). Dry run
  by default.

.DESCRIPTION
  A root is the tree root itself (-Root) or any folder directly containing a siteoffice.json
  manifest (a unit root). At each root, the default rule renames three folder names when found:
  projects -> 50_projects, docs -> 90_docs at the tree root or 05_docs at a unit root,
  graphify-out -> _graphify. Supply -Map (a CSV with columns Old,New,Kind; Kind one of
  root, unit, tool) to override or extend the default map; a Kind of tool means a tool-owned
  name that is reported but never renamed (the graphify caveat can turn graphify-out into one).

  Order of operation: inventory scan for every hard-coded mention of an old path (in file
  content, all common path spellings) under -Root, excluding history files (decisions.md,
  SESSION_LOG.md, anything under _archive, handovers, or 03_logs\_history, which may quote old
  paths on purpose); print the stop list of outside-tree pointers this script cannot fix; on
  -Apply, move each folder (git mv inside a git repository, Move-Item outside one), then rewrite
  the text hits via Invoke-SiteofficeRewrite.ps1 against a synthetic migration map with
  oldRoot = newRoot = -Root; then a verification inventory expecting zero hits for the old
  paths outside the excluded history files.

  Refuses to -Apply (dry run still runs, with a warning) while any .worktrees folder under the
  root holds a linked worktree, or any _board\board.json under the root was written in the last
  10 minutes (a render may be in flight).

  v2 (2026-10-01), after the real run on a 47k-file estate:
  - A folder with no tracked files (gitignored or untracked) cannot be git mv'd; it is moved as a
    plain folder, logged, and a warning follows when the old name was ignored and the new one is not.
  - The rewrite script is copied to a temp folder before anything moves, because $PSScriptRoot can
    sit inside a folder this run renames.
  - Nested renames compose: a child renamed inside a renamed parent (projects\x\docs becomes
    50_projects\x\05_docs) is rewritten to its final path, in text and for the files inside it.
  - Also found and rewritten: relative forms (<old>\<name>, ..\..\<old>\<name>, forward-slash keys
    in a .tsv ledger), and .tsv, .bat and .cmd files. A relative form is kept only if a renamed
    folder is on its path and the next segment exists on disk next to the file or an ancestor.
    Files that are not valid UTF-8 are skipped and listed, never rewritten.
  - v3 (2026-10-01): the renamed folder may be any segment of a relative form (<unit>\<old>\<name>),
    not only the first. The relative-form code lives in SiteofficeRelativeForms.ps1 (shared with
    Invoke-SiteofficeRewrite.ps1), dot-sourced at the top so it loads before the first move.
    These examples use placeholders on purpose: a real-looking path in a comment is rewritten when
    this script sits inside the tree it renames.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252, and this
  script's own inventory/rewrite calls run against files that share that constraint.

.EXAMPLE
  PowerShell on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeRenameToRule.ps1 -Root 'C:\estate' -OutDir 'C:\estate\04_engineering\migration'
    .\Invoke-SiteofficeRenameToRule.ps1 -Root 'C:\estate' -OutDir 'C:\estate\04_engineering\migration' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [string]$Map = '',
  [switch]$Apply,
  [string]$OutDir = '',
  [string[]]$Extensions = @('.md','.json','.ps1','.py','.html','.toml','.yml','.yaml','.tsv','.bat','.cmd'),
  [string[]]$ExcludeDirs = @('.git','.worktrees','node_modules','__pycache__','.venv','venv','dist','build'),
  [string[]]$HistoryFiles = @('decisions.md','SESSION_LOG.md'),
  [string[]]$HistoryDirs = @('_archive','handovers','03_logs\_history'),
  [int]$MaxFileMB = 5,
  [int]$BoardFreshMinutes = 10,
  [string]$RewriteScript = '',
  [switch]$GraphifyHasOutputOption
)

$ErrorActionPreference = 'Stop'
# Loaded now, while $PSScriptRoot is still valid: the folder holding this script may be renamed below.
. (Join-Path $PSScriptRoot 'SiteofficeRelativeForms.ps1')
$script:moves = New-Object System.Collections.Generic.List[object]

function Test-DotExcluded {
  param([string]$Name)
  return ($Name.StartsWith('.') -and $Name -ne '.claude')
}

function Test-ExcludedDir {
  param([string]$FullPath, [string]$Root)
  foreach ($d in $ExcludeDirs) { if ($FullPath -like "*\$d\*" -or $FullPath -like "*\$d") { return $true } }
  foreach ($h in $HistoryDirs) { if ($FullPath -like "*\$h\*" -or $FullPath -like "*\$h") { return $true } }
  $rel = $FullPath.Substring($Root.Length).TrimStart('\')
  foreach ($seg in ($rel -split '\\')) { if (Test-DotExcluded $seg) { return $true } }
  return $false
}

function Get-UnitRoots {
  # $Root itself, plus every folder anywhere under it that directly holds siteoffice.json
  param([string]$Root)
  $roots = New-Object System.Collections.Generic.List[string]
  $roots.Add($Root)
  Get-ChildItem -LiteralPath $Root -Recurse -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { -not (Test-ExcludedDir $_.FullName $Root) } |
    ForEach-Object {
      if (Test-Path -LiteralPath (Join-Path $_.FullName 'siteoffice.json')) { $roots.Add($_.FullName) }
    }
  return $roots
}

function Get-DefaultMap {
  param([string]$Root)
  $rows = New-Object System.Collections.Generic.List[object]
  $roots = Get-UnitRoots -Root $Root
  foreach ($r in $roots) {
    $isEstateRoot = ($r -eq $Root)
    $kind = if ($isEstateRoot) { 'root' } else { 'unit' }
    $projOld = Join-Path $r 'projects'
    if (Test-Path -LiteralPath $projOld -PathType Container) {
      $rows.Add([pscustomobject]@{ Old = $projOld; New = (Join-Path $r '50_projects'); Kind = $kind })
    }
    $docsOld = Join-Path $r 'docs'
    if (Test-Path -LiteralPath $docsOld -PathType Container) {
      $newName = if ($isEstateRoot) { '90_docs' } else { '05_docs' }
      $rows.Add([pscustomobject]@{ Old = $docsOld; New = (Join-Path $r $newName); Kind = $kind })
    }
    $graphOld = Join-Path $r 'graphify-out'
    if (Test-Path -LiteralPath $graphOld -PathType Container) {
      # graphify caveat: 'graphify update' (the command sessions actually run) has no
      # output-directory flag, only 'merge-graphs' does (checked 2026-09-11). graphify-out is
      # therefore tool-owned like .git; -GraphifyHasOutputOption flips this if that ever changes.
      $graphKind = if ($GraphifyHasOutputOption) { $kind } else { 'tool' }
      $rows.Add([pscustomobject]@{ Old = $graphOld; New = (Join-Path $r '_graphify'); Kind = $graphKind })
    }
  }
  return $rows
}

function Resolve-MapPaths {
  # -Map rows may give Old/New as absolute or relative-to-Root; normalise to absolute.
  param([object[]]$Rows, [string]$Root)
  foreach ($row in $Rows) {
    if (-not [IO.Path]::IsPathRooted($row.Old)) { $row.Old = Join-Path $Root $row.Old }
    if ($row.New -and -not [IO.Path]::IsPathRooted($row.New)) { $row.New = Join-Path $Root $row.New }
    $row.Old = $row.Old.TrimEnd('\')
    if ($row.New) { $row.New = $row.New.TrimEnd('\') }
  }
  return $Rows
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
  # scans $Root for text mentions of every Old path in $Rows (any spelling). Returns inventory rows.
  param([object[]]$Rows, [string]$Root)
  $out = New-Object System.Collections.Generic.List[object]
  if ($Rows.Count -eq 0) { return $out }
  $spellings = @()
  foreach ($row in $Rows) { $spellings += (Get-Spellings $row.Old) }
  $alts = ($spellings | ForEach-Object { [regex]::Escape($_) }) -join '|'
  # boundary: a safe punctuation/whitespace char, end of string, or a sentence-ending dot not
  # itself followed by more name characters (so ...\old-name.md is not a match of ...\old-name).
  $pattern = "(?i)(?:$alts)(?=[\\/`"'\s\)\]\>,;:]|\.(?![A-Za-z0-9])|$)"
  $rx = New-Object System.Text.RegularExpressions.Regex ($pattern)
  $extSet = @{}
  foreach ($e in $Extensions) { $extSet[$e.ToLower()] = $true }
  $maxBytes = $MaxFileMB * 1MB

  Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
      (-not (Test-ExcludedDir $_.DirectoryName $Root)) -and $extSet.ContainsKey($_.Extension.ToLower()) -and $_.Length -le $maxBytes
    } |
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
          $out.Add([pscustomobject]@{
            Source  = 'file'
            Path    = $f.FullName
            Line    = $h.LineNumber
            Match   = $m.Value
            Context = $ctx
            Note    = $note
          })
        }
      }
    }
  return $out
}

function Get-FinalPath {
  # A path as it reads after ALL renames in $Rows ran. A nested row's New is written against its
  # parent's OLD name (projects\x\docs -> projects\x\05_docs) and the parent is renamed later in the
  # same run, so apply the rows deepest first: the child, then each ancestor.
  param([string]$Path, [object[]]$Rows)
  foreach ($row in ($Rows | Sort-Object { $_.Old.Split('\').Count } -Descending)) {
    if ($Path.Equals($row.Old, [StringComparison]::OrdinalIgnoreCase)) { $Path = $row.New }
    elseif ($Path.StartsWith($row.Old + '\', [StringComparison]::OrdinalIgnoreCase)) { $Path = $row.New + $Path.Substring($row.Old.Length) }
  }
  return $Path
}

function Test-ActiveWorktree {
  param([string]$Root)
  $hits = @()
  Get-ChildItem -LiteralPath $Root -Recurse -Directory -Force -Filter '.worktrees' -ErrorAction SilentlyContinue |
    Where-Object { -not (Test-ExcludedDir $_.FullName $Root) } |
    ForEach-Object {
      $children = Get-ChildItem -LiteralPath $_.FullName -Directory -Force -ErrorAction SilentlyContinue
      if ($children.Count -gt 0) { $hits += $_.FullName }
    }
  return $hits
}

function Test-FreshBoardRender {
  param([string]$Root, [int]$Minutes)
  $hits = @()
  Get-ChildItem -LiteralPath $Root -Recurse -Force -Filter 'board.json' -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -like '*\_board' } |
    ForEach-Object {
      if ($_.LastWriteTime -gt (Get-Date).AddMinutes(-$Minutes)) { $hits += $_.FullName }
    }
  return $hits
}

function Find-GitRepoRoot {
  # never walks above $Boundary: a folder under an unrelated outer repo (e.g. this repo's own
  # _scratch test trees) must not be git mv'd against that outer repo.
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

function Write-StopList {
  param([string]$Root)
  Write-Host ''
  Write-Host '== Stop list: paths outside the tree that point in. This mode warns, it does not fix these.'
  $items = @(
    'Cowork project instructions and mounted folders (the app store the UI cannot edit)',
    '.mcp.json server roots, if any server root path is a folder this run renames',
    'Windows Task Scheduler actions that pass a folder this run renames',
    'skill routing files outside this tree (routing.json entries are inside the tree and rewritten)',
    'the Claude memory directory (per-session facts that may quote an old path)'
  )
  foreach ($i in $items) { Write-Host "  - $i" }
  $mcp = Join-Path $Root '.mcp.json'
  if (Test-Path -LiteralPath $mcp) { Write-Host "  found: $mcp (read it, check server roots by hand)" }
  $claudeJson = Join-Path $env:USERPROFILE '.claude.json'
  if (Test-Path -LiteralPath $claudeJson) { Write-Host "  found: $claudeJson (Claude Code per-path state; not read here)" }
  Write-Host ''
}

try {
  $Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
  if ($Root.Length -lt 3 -or $Root.Substring(1,2) -ne ':\') { throw "Root must be a drive path like C:\estate, got '$Root'" }
  if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = Join-Path $Root '10_holding\04_engineering\migration' }
  if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
  if ([string]::IsNullOrWhiteSpace($RewriteScript)) { $RewriteScript = Join-Path $PSScriptRoot 'Invoke-SiteofficeRewrite.ps1' }
  $stamp = Get-Date -Format 'yyyy-MM-dd'
  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  Write-Host "== Siteoffice rename-to-rule, $mode, $stamp"
  Write-Host "Root: $Root"
  Write-Host "OutDir: $OutDir"
  Write-Host ''

  # --- preconditions -----------------------------------------------------------
  $wt = Test-ActiveWorktree -Root $Root
  $freshBoard = Test-FreshBoardRender -Root $Root -Minutes $BoardFreshMinutes
  if ($wt.Count -gt 0 -or $freshBoard.Count -gt 0) {
    Write-Host 'PRECONDITION: possible work in flight.'
    foreach ($w in $wt) { Write-Host "  active worktree under: $w" }
    foreach ($b in $freshBoard) { Write-Host "  board render in flight (younger than $BoardFreshMinutes min): $b" }
    if ($Apply) {
      Write-Host ''
      Write-Host 'REFUSED: -Apply blocked while work may be in flight. Re-run after it clears, or without -Apply to keep reading.'
      exit 1
    } else {
      Write-Host '  (dry run continues; -Apply would refuse on this until it clears)'
    }
  }
  Write-Host ''

  # --- the map -------------------------------------------------------------------
  if ([string]::IsNullOrWhiteSpace($Map)) {
    $rows = Get-DefaultMap -Root $Root
    $mapCsv = Join-Path $OutDir "${stamp}_rename_to_rule_map.csv"
    $rows | Select-Object Old, New, Kind | Export-Csv -LiteralPath $mapCsv -NoTypeInformation -Encoding UTF8
    Write-Host "Default map (no -Map given), written to: $mapCsv"
  } else {
    $mapCsv = (Resolve-Path -LiteralPath $Map).Path
    $rows = Import-Csv -LiteralPath $mapCsv
    Write-Host "Map read from: $mapCsv"
  }
  $rows = Resolve-MapPaths -Rows $rows -Root $Root

  Write-Host ''
  Write-Host 'Rename map:'
  foreach ($row in $rows) {
    $exists = Test-Path -LiteralPath $row.Old -PathType Container
    $status = if (-not $exists) { '(not found, skipped)' } elseif ($row.Kind -eq 'tool') { '(tool-owned name, not renamed)' } else { '' }
    Write-Host ("  {0,-6} {1}  ->  {2}  {3}" -f $row.Kind, $row.Old, $row.New, $status)
  }
  $moveRows = @($rows | Where-Object { $_.Kind -ne 'tool' -and (Test-Path -LiteralPath $_.Old -PathType Container) })
  if ($moveRows.Count -eq 0) { Write-Host ''; Write-Host 'Nothing to rename under this root with this map.' }

  # --- inventory (pre-move) -------------------------------------------------------
  Write-Host ''
  Write-Host 'Scanning for hard-coded mentions of the old paths ...'
  $inv = Find-PathMentions -Rows $moveRows -Root $Root
  $invCsv = Join-Path $OutDir "${stamp}_rename_to_rule_inventory.csv"
  $inv | Export-Csv -LiteralPath $invCsv -NoTypeInformation -Encoding UTF8
  $histCount = @($inv | Where-Object { $_.Note -like 'history file*' }).Count
  Write-Host ("  file hits: {0} ({1} in history files, excluded from rewrite)" -f $inv.Count, $histCount)
  Write-Host "  inventory: $invCsv"

  $extSetRel = @{}
  foreach ($e in $Extensions) { $extSetRel[$e.ToLower()] = $true }
  $relFiles = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { (-not (Test-ExcludedDir $_.DirectoryName $Root)) -and $extSetRel.ContainsKey($_.Extension.ToLower()) -and $_.Length -le ($MaxFileMB * 1MB) })
  $relSkippedNonUtf8 = New-Object System.Collections.Generic.List[string]
  $rel = @(Find-RelativeMentions -File $relFiles -Root $Root -Rows $moveRows -MapPath { param($p) Get-FinalPath -Path $p -Rows $moveRows } -HistoryFiles $HistoryFiles -SkippedNonUtf8 $relSkippedNonUtf8)
  $relCsv = Join-Path $OutDir "${stamp}_rename_to_rule_inventory_relative.csv"
  $rel | Export-Csv -LiteralPath $relCsv -NoTypeInformation -Encoding UTF8
  $relHist = @($rel | Where-Object { $_.Note -like 'history file*' }).Count
  $relReview = @($rel | Where-Object { $_.Note -like 'review*' }).Count
  Write-Host ("  relative-form hits: {0} ({1} in history files, excluded from rewrite; {2} to review by hand)" -f $rel.Count, $relHist, $relReview)
  Write-Host "  relative inventory: $relCsv"
  foreach ($nf in $relSkippedNonUtf8) { Write-Host "  not UTF-8, never rewritten, edit by hand: $nf" }

  Write-StopList -Root $Root

  if ($moveRows.Count -eq 0) {
    Write-Host 'Nothing to move. Exiting.'
    exit 0
  }

  # Windows refuses to rename a folder that is a process's working directory ("Permission denied"
  # halfway through the moves). Check this process and the shell that launched it, before any move.
  $cwds = @((Get-Location).ProviderPath, [Environment]::CurrentDirectory) | Select-Object -Unique
  $held = @(foreach ($row in $moveRows) {
    foreach ($c in $cwds) {
      if ($c.Equals($row.Old, [StringComparison]::OrdinalIgnoreCase) -or $c.StartsWith($row.Old + '\', [StringComparison]::OrdinalIgnoreCase)) { $row.Old }
    }
  }) | Select-Object -Unique
  if ($held.Count -gt 0) {
    Write-Host ''
    Write-Host 'WORKING DIRECTORY INSIDE A FOLDER THIS RUN RENAMES:'
    foreach ($h in $held) { Write-Host "  $h" }
    Write-Host "  cd to a folder outside the tree (for example C:\) and call this script by its full path."
    if ($Apply) { throw 'REFUSED: the working directory is inside a folder that would be renamed; nothing was moved.' }
  }

  if (-not $Apply) {
    Write-Host ''
    Write-Host 'Dry run only. Nothing changed. Re-run with -Apply after reading the map and the inventory.'
    exit 0
  }

  # --- apply: stage the rewrite script outside the tree ------------------------
  # $PSScriptRoot can be inside a folder this run renames (the skill checked out under projects\),
  # so the path resolved above is dead once the move has happened. Run a copy from the temp folder.
  if (-not (Test-Path -LiteralPath $RewriteScript)) { throw "Rewrite script not found: $RewriteScript" }
  $stageDir = Join-Path ([IO.Path]::GetTempPath()) ('siteoffice-rtr-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $stageDir | Out-Null
  Copy-Item -LiteralPath $RewriteScript -Destination $stageDir
  $RewriteScript = Join-Path $stageDir (Split-Path -Leaf $RewriteScript)

  # --- apply: move ------------------------------------------------------------
  Write-Host ''
  Write-Host 'Applying moves ...'
  $movesLog = Join-Path $OutDir "${stamp}_rename_to_rule_moves.log"
  foreach ($row in ($moveRows | Sort-Object { $_.Old.Split('\').Count } -Descending)) {
    $repo = Find-GitRepoRoot -Path $row.Old -Boundary $Root
    if (Test-Path -LiteralPath $row.New) {
      $line = "SKIP    $($row.Old) -> $($row.New)  (target already exists)"
      Write-Host "  $line"; $script:moves.Add($line); continue
    }
    # A folder with no tracked files (gitignored or untracked) cannot be git mv'd ("source directory
    # is empty", exit 128). Move it as a plain folder and say so.
    $plainRepo = $null; $ignoredBefore = $false
    if ($repo) {
      $relOld = $row.Old.Substring($repo.Length + 1)
      $relNew = $row.New.Substring($repo.Length + 1)
      $tracked = & git -C $repo ls-files -- $relOld 2>$null
      if (-not $tracked) {
        $plainRepo = $repo; $repo = $null
        & git -C $plainRepo check-ignore -q -- $relOld 2>$null
        $ignoredBefore = ($LASTEXITCODE -eq 0)
      }
    }
    if ($repo) {
      Write-Host "  git mv   $($row.Old) -> $($row.New)  (repo: $repo)"
      $out = & git -C $repo mv -- $relOld $relNew 2>&1
      $rc = $LASTEXITCODE
      $line = "git mv  $($row.Old) -> $($row.New)  repo=$repo  exit=$rc  $($out -join ' ')"
    } else {
      Write-Host "  move     $($row.Old) -> $($row.New)"
      Move-Item -LiteralPath $row.Old -Destination $row.New
      $rc = 0
      $line = "move    $($row.Old) -> $($row.New)  exit=$rc"
      if ($plainRepo) {
        $line += "  note=no tracked files in repo $plainRepo, plain move"
        Write-Host "           (no tracked files in $plainRepo for this folder: plain move, no git history to carry)"
        if ($ignoredBefore) {
          & git -C $plainRepo check-ignore -q -- $relNew 2>$null
          if ($LASTEXITCODE -ne 0) {
            $line += '  WARNING=ignored under the old name, not under the new one'
            Write-Host "  WARNING: $relOld was gitignored, $relNew is not. Update .gitignore in $plainRepo or git lists the folder as untracked."
          }
        }
      }
    }
    $script:moves.Add($line)
    if ($rc -ne 0) { throw "move failed for $($row.Old): $line" }
  }
  $script:moves | Set-Content -LiteralPath $movesLog -Encoding UTF8
  Write-Host "  moves log: $movesLog"

  # --- apply: rewrite via the existing rewrite script --------------------------
  Write-Host ''
  Write-Host 'Rewriting text hits via Invoke-SiteofficeRewrite.ps1 ...'
  # Relative forms first: their offsets were taken before any text changed.
  $relApply = @($rel | Where-Object { $_.Note -eq '' })
  if ($relApply.Count -gt 0) {
    $relReport = Invoke-RelativeRewrite -RelRows $relApply -MapPath { param($p) Get-FinalPath -Path $p -Rows $moveRows }
    $relReportCsv = Join-Path $OutDir "${stamp}_rename_to_rule_relative_report.csv"
    $relReport | Export-Csv -LiteralPath $relReportCsv -NoTypeInformation -Encoding UTF8
    $relDone = @($relReport | Where-Object { $_.Status -eq 'rewritten' }).Count
    Write-Host ("  relative forms rewritten: {0} of {1}; report: {2}" -f $relDone, $relReport.Count, $relReportCsv)
  }
  $entries = @()
  foreach ($row in $moveRows) {
    # new = the FINAL path, so a child renamed inside a renamed parent composes (see Get-FinalPath).
    # The moves above still use row.New, deepest first.
    $entries += [ordered]@{
      old  = $row.Old.Substring($Root.Length + 1)
      new  = (Get-FinalPath -Path $row.New -Rows $moveRows).Substring($Root.Length + 1)
      type = 'folder'
      git  = 'none'
      note = "rename-to-rule: $($row.Kind)"
    }
  }
  $synthMap = [ordered]@{
    schemaVersion    = 1
    oldRoot          = $Root
    newRoot          = $Root
    holdingIsCompany = $false
    staysSubfolders  = @()
    rollbackDays     = 0
    entries          = $entries
  }
  $synthMapPath = Join-Path $OutDir "${stamp}_rename_to_rule_synthetic_map.json"
  ($synthMap | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $synthMapPath -Encoding UTF8
  # History-file hits are excluded here, not passed through -SkipNotesLike: an array parameter
  # crossing a nested pwsh -File process boundary is not reliable enough to depend on for a
  # rule that must hold (a history file must never be rewritten).
  $rewriteRows = @($inv | Where-Object { $_.Note -notlike 'history file*' })
  $rewriteInvCsv = Join-Path $OutDir "${stamp}_rename_to_rule_inventory_for_rewrite.csv"
  $rewriteRows | Export-Csv -LiteralPath $rewriteInvCsv -NoTypeInformation -Encoding UTF8
  $rewriteReport = Join-Path $OutDir "${stamp}_rename_to_rule_rewrite_report.csv"
  & pwsh -NoProfile -File $RewriteScript -Inventory $rewriteInvCsv -Map $synthMapPath -Apply -ReportCsv $rewriteReport -NoRelativeForms
  $rc = $LASTEXITCODE
  if ($rc -ne 0) { throw "Invoke-SiteofficeRewrite.ps1 exited $rc" }
  Write-Host "  rewrite report: $rewriteReport"

  # --- verification --------------------------------------------------------------
  Write-Host ''
  Write-Host 'Verification: re-scanning for old-path mentions ...'
  $verifyRows = @($moveRows | ForEach-Object { [pscustomobject]@{ Old = $_.Old; New = $_.New; Kind = $_.Kind } })
  $verify = Find-PathMentions -Rows $verifyRows -Root $Root
  $verifyCsv = Join-Path $OutDir "${stamp}_rename_to_rule_verify.csv"
  $verify | Export-Csv -LiteralPath $verifyCsv -NoTypeInformation -Encoding UTF8
  $remaining = @($verify | Where-Object { $_.Note -notlike 'history file*' })
  Write-Host ("  remaining hits (excluding history files): {0}" -f $remaining.Count)
  Write-Host "  verify CSV: $verifyCsv"
  if ($remaining.Count -gt 0) {
    Write-Host ''
    Write-Host 'Rows remaining after rewrite (named exceptions or a script that derives the old path, read by hand):'
    $remaining | Select-Object -First 20 | ForEach-Object { Write-Host ("    {0}:{1}  {2}" -f $_.Path, $_.Line, $_.Match) }
  }

  Write-Host ''
  Write-Host 'Done.'
  exit 0
}
catch {
  Write-Host ("RENAME-TO-RULE FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
finally {
  if ($stageDir -and (Test-Path -LiteralPath $stageDir)) { Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue }
}
