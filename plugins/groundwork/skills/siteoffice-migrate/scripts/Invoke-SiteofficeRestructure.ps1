#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, restructure and relocate modes. Copies a grown folder tree onto the
  Siteoffice layout at a new root, driven by an approved migration_map.json. Dry run by default.

.DESCRIPTION
  Reads the map (schema: ..\references\migration_map_schema.md), validates it, and for every
  entry prints the plan: robocopy source to target, skeleton files a node lacks, manifests to
  write. With -Apply it executes the plan. It never deletes and never overwrites an existing
  file. The old root is left in place for the rollback window named in the map.

  v1 limits, said out loud: entries with "git": "clone" are copied as folders (working tree and
  .git included), not re-cloned; the remote is listed so the user can verify. Paths inside the
  copied files are NOT rewritten here; that is Invoke-SiteofficeRewrite.ps1, run after this.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop, dry run:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeRestructure.ps1 -Map 'C:\old-root\hub\04_engineering\migration\2026-01-31_migration_map.json'
  Then, after reading the plan:
    .\Invoke-SiteofficeRestructure.ps1 -Map '<same>' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Map,
  [switch]$Apply,
  [string]$PlanDir = ''
)

$ErrorActionPreference = 'Stop'
$today = Get-Date -Format 'yyyy-MM-dd'
$plan = New-Object System.Collections.Generic.List[string]
$nodeTypes = @('holding','company','department','project')
$containerTypes = @('holding','company','department')
$copyTypes = @('holding','company','department','project','packs','board','log','funnel','archive','tools','data')
$reportOnly = @('stays','delete','generated')

function Say { param([string]$s) $plan.Add($s); Write-Host $s }

function New-FileIfMissing {
  param([string]$Path, [string]$Content)
  if (Test-Path -LiteralPath $Path) { return $false }
  if ($Apply) {
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
  }
  return $true
}

function New-DirIfMissing {
  param([string]$Path)
  if (Test-Path -LiteralPath $Path) { return $false }
  if ($Apply) { New-Item -ItemType Directory -Path $Path -Force | Out-Null }
  return $true
}

function Add-UnitSkeleton {
  param([string]$NodePath, [string]$Type, [string]$Name, [string]$Parent, [string]$Language)
  $added = New-Object System.Collections.Generic.List[string]
  $tbf = "To be filled, $today."
  # instruction file: keep whatever exists; create the pair only when neither exists
  $hasAgents = Test-Path -LiteralPath (Join-Path $NodePath 'AGENTS.md')
  $hasClaude = Test-Path -LiteralPath (Join-Path $NodePath 'CLAUDE.md')
  $instr = 'AGENTS.md'
  if ($hasClaude -and -not $hasAgents) { $instr = 'CLAUDE.md' }
  if (-not $hasAgents -and -not $hasClaude) {
    if (New-FileIfMissing (Join-Path $NodePath 'AGENTS.md') "# $Name`r`n`r`nInstruction file for this $Type. $tbf`r`n") { $added.Add('AGENTS.md') }
    if (New-FileIfMissing (Join-Path $NodePath 'CLAUDE.md') "@AGENTS.md`r`n") { $added.Add('CLAUDE.md') }
  }
  $files = @{
    'STATUS.md'              = "# $Name, status`r`n`r`nWhat is true now. $tbf`r`n"
    'DEADLINES.md'           = "# $Name, deadlines`r`n`r`nHard dates only, most imminent on top. None known, asked $today.`r`n"
    'SESSION_LOG.md'         = "# $Name, session log`r`n`r`nOne paragraph per work session, newest on top.`r`n"
    '00_context\brief.md'    = "# $Name, brief`r`n`r`nWhat this node is, who owns it, who approves. $tbf`r`n"
    '02_roadmap\roadmap.md'  = "# $Name, roadmap`r`n`r`nPriority order lives here and only here. $tbf`r`n"
    '03_logs\decisions.md'   = "# $Name, decisions`r`n`r`nDecisions with their why, newest on top.`r`n"
  }
  foreach ($k in $files.Keys) {
    if (New-FileIfMissing (Join-Path $NodePath $k) $files[$k]) { $added.Add($k) }
  }
  foreach ($d in @('03_logs\_history','04_assets')) {
    if (New-DirIfMissing (Join-Path $NodePath $d)) { $added.Add($d + '\') }
  }
  if ($containerTypes -contains $Type) {
    if (New-DirIfMissing (Join-Path $NodePath '01_projects')) { $added.Add('01_projects\') }
    if (New-FileIfMissing (Join-Path $NodePath '03_logs\hub_inbox.md') "# $Name, hub inbox`r`n`r`nIncoming briefs from child nodes, newest on top.`r`n") { $added.Add('03_logs\hub_inbox.md') }
  }
  $manifest = Join-Path $NodePath 'siteoffice.json'
  if (-not (Test-Path -LiteralPath $manifest)) {
    $m = [ordered]@{
      schemaVersion   = 1
      type            = $Type
      name            = $Name
      parent          = $Parent
      children        = @()
      language        = $Language
      approver        = ''
      instructionFile = $instr
      setup           = 'structure-only'
      migratedOn      = $today
    }
    if (New-FileIfMissing $manifest (($m | ConvertTo-Json -Depth 4) + "`r`n")) { $added.Add('siteoffice.json') }
  }
  return $added
}

try {
  $mapPath = (Resolve-Path -LiteralPath $Map).Path
  $m = Get-Content -LiteralPath $mapPath -Raw | ConvertFrom-Json
  $oldRoot = $m.oldRoot.TrimEnd('\')
  $newRoot = $m.newRoot.TrimEnd('\')
  $lang = if ($m.language) { $m.language } else { 'en' }
  $holdingName = if ($m.holdingName) { $m.holdingName } else { Split-Path -Leaf $newRoot }
  $excludeDirs = @()
  if ($m.excludeDirs) { $excludeDirs = @($m.excludeDirs) }
  if ($excludeDirs -notcontains '.worktrees') { $excludeDirs += '.worktrees' }

  # --- validation --------------------------------------------------------------
  if (-not (Test-Path -LiteralPath $oldRoot)) { throw "oldRoot does not exist: $oldRoot" }
  if ($newRoot -match '(?i)\\(OneDrive|Dropbox|iCloudDrive|Google Drive)(\\|$)') { throw "newRoot sits inside a cloud-synced folder: $newRoot. Refused." }
  if ($newRoot.ToLower().StartsWith($oldRoot.ToLower() + '\')) { throw "newRoot is inside oldRoot. Refused." }
  $classify = @($m.entries | Where-Object { $_.type -eq 'classify' })
  if ($classify.Count -gt 0) { throw ("{0} entries are still 'classify': {1}. Decide them first." -f $classify.Count, (($classify | ForEach-Object { $_.old }) -join ', ')) }
  foreach ($e in $m.entries) {
    if ($e.old -ne '' -and -not [IO.Path]::IsPathRooted($e.old)) { $e.old = Join-Path $oldRoot $e.old }
    if ($e.old -ne '' -and ($reportOnly -notcontains $e.type) -and -not (Test-Path -LiteralPath $e.old)) { throw "entry source missing on disk: $($e.old)" }
  }

  if ([string]::IsNullOrWhiteSpace($PlanDir)) { $PlanDir = Join-Path $env:TEMP 'siteoffice-migrate' }
  if (-not (Test-Path -LiteralPath $PlanDir)) { New-Item -ItemType Directory -Path $PlanDir -Force | Out-Null }
  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  Say "== Siteoffice restructure, $mode, $today"
  Say "oldRoot: $oldRoot"
  Say "newRoot: $newRoot   holdingIsCompany: $($m.holdingIsCompany)   rollbackDays: $($m.rollbackDays)"
  Say ("excluded dir names (regenerable, not copied): {0}" -f ($excludeDirs -join ', '))
  Say ''

  # --- holding root ------------------------------------------------------------
  if (New-DirIfMissing $newRoot) { Say "mkdir  $newRoot" }
  foreach ($d in @('00_funnel','_packs','_board','_log')) {
    $p = Join-Path $newRoot $d
    if (New-DirIfMissing $p) { Say "mkdir  $p" }
    $readme = @{
      '00_funnel' = 'Cross-folder ideas funnel. One file per item, author name in the filename, append-only inbox.'
      '_packs'    = 'Installed expertise packs. Own packs under own\.'
      '_board'    = 'Rendered board, generated. Do not hand-edit.'
      '_log'      = 'Activity log, receipts, gate verdicts, runs. Append-only, one file per event.'
    }[$d]
    if (New-FileIfMissing (Join-Path $p 'README.md') "# $d`r`n`r`n$readme`r`n") { Say "create $p\README.md" }
  }

  # --- entries -----------------------------------------------------------------
  $copyCmds = New-Object System.Collections.Generic.List[string]
  $nodes = New-Object System.Collections.Generic.List[object]
  foreach ($e in $m.entries) {
    $target = if ($e.new -eq '') { $newRoot } else { Join-Path $newRoot $e.new }
    # note: 'continue' inside a switch does not continue the foreach in PowerShell, hence the flag
    $skipEntry = $false
    switch ($e.type) {
      'stays'     { Say "stays  $($e.old)  (not moved: $($e.note))"; $skipEntry = $true }
      'delete'    { Say "delete $($e.old)  (NOT done by this script; user's step: $($e.note))"; $skipEntry = $true }
      'generated' { Say "regen  $($e.old)  (not copied; regenerate at the new root: $($e.note))"; $skipEntry = $true }
    }
    if ($skipEntry) { continue }
    if ($e.old -ne '' -and (Test-Path -LiteralPath $e.old -PathType Leaf)) {
      # a single file entry (root-level loose files): plain copy, never overwrite
      Say ("file   {0}  ->  {1}   [{2}]" -f $e.old, $target, $e.type)
      if ($Apply) {
        if (Test-Path -LiteralPath $target) { Say "       exists at target, not overwritten" }
        else {
          $tdir = Split-Path -Parent $target
          if (-not (Test-Path -LiteralPath $tdir)) { New-Item -ItemType Directory -Path $tdir -Force | Out-Null }
          Copy-Item -LiteralPath $e.old -Destination $target
        }
      }
      continue
    }
    if ($e.old -ne '') {
      $roboLog = Join-Path $PlanDir ("robocopy_{0}.log" -f (($e.new -replace '[\\/:]', '_') -replace '^$', 'root'))
      $rargs = @("`"$($e.old)`"", "`"$target`"", '/E', '/COPY:DAT', '/DCOPY:T', '/XJ', '/R:1', '/W:1', '/NFL', '/NDL', '/NP', "/LOG:`"$roboLog`"")
      if ($excludeDirs.Count -gt 0) { $rargs += '/XD'; $rargs += $excludeDirs }
      if (-not $Apply) { $rargs += '/L' }
      $cmd = 'robocopy ' + ($rargs -join ' ')
      Say ("copy   {0}  ->  {1}   [{2}, git={3}]" -f $e.old, $target, $e.type, $e.git)
      if ($e.git -eq 'clone' -or $e.git -eq 'copy') {
        $cfg = Join-Path $e.old '.git\config'
        if (Test-Path -LiteralPath $cfg) {
          $remote = (Select-String -LiteralPath $cfg -Pattern '^\s*url\s*=\s*(.+)$' | Select-Object -First 1)
          if ($remote) { Say ("       remote: {0}  (v1 copies the folder; verify with git remote -v afterwards)" -f $remote.Matches[0].Groups[1].Value.Trim()) }
        }
      }
      $copyCmds.Add($cmd)
      if ($Apply) {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try {
          # one argument line, paths already quoted: avoids PowerShell 7 native-argument rewriting
          $proc = Start-Process -FilePath 'robocopy.exe' -ArgumentList ($rargs -join ' ') -Wait -PassThru -NoNewWindow
          $rc = $proc.ExitCode
        } finally { $ErrorActionPreference = $prev }
        if ($rc -ge 8) {
          $tail = ''
          if (Test-Path -LiteralPath $roboLog) { $tail = (Get-Content -LiteralPath $roboLog -Tail 12) -join "`r`n" }
          throw "robocopy failed ($rc) for $($e.old); log $roboLog`r`n$tail"
        }
        Say ("       robocopy exit {0} (0-7 is success)" -f $rc)
      }
    } else {
      if (New-DirIfMissing $target) { Say "mkdir  $target  [new $($e.type): $($e.note)]" }
    }
    if ($nodeTypes -contains $e.type) {
      $name = Split-Path -Leaf $target
      $parent = if ($e.type -eq 'holding') { '' } else { $holdingName }
      $added = Add-UnitSkeleton -NodePath $target -Type $e.type -Name $name -Parent $parent -Language $lang
      if ($added.Count -gt 0) { Say ("       skeleton added: {0}" -f ($added -join ', ')) }
      $nodes.Add([pscustomobject]@{ name = $name; type = $e.type; path = $e.new })
    }
  }

  # --- root manifest -----------------------------------------------------------
  $rootManifest = Join-Path $newRoot 'siteoffice.json'
  $rollbackDays = 14.0
  if ($null -ne $m.rollbackDays) { $rollbackDays = [double]"$($m.rollbackDays)" }
  $rollbackUntil = [DateTime]::Now.AddDays($rollbackDays).ToString('yyyy-MM-dd')
  $isCompany = $false
  if ($null -ne $m.holdingIsCompany) { $isCompany = [bool]$m.holdingIsCompany }
  $nodeArray = @(); foreach ($n in $nodes) { $nodeArray += $n }
  $rm = [ordered]@{
    schemaVersion    = 1
    type             = 'holding'
    name             = $holdingName
    holdingIsCompany = $isCompany
    language         = $lang
    nodes            = $nodeArray
    migratedFrom     = $oldRoot
    migratedOn       = $today
    rollbackUntil    = $rollbackUntil
    setup            = 'structure-only'
  }
  if (New-FileIfMissing $rootManifest (($rm | ConvertTo-Json -Depth 5) + "`r`n")) { Say "create $rootManifest" }
  else { Say "keep   $rootManifest (exists; not overwritten)" }

  # --- plan and rollback note --------------------------------------------------
  $planFile = Join-Path $PlanDir ("{0}_restructure_{1}.txt" -f $today, ($mode -replace ' ', '').ToLower())
  $plan | Set-Content -LiteralPath $planFile -Encoding UTF8
  $cmdFile = Join-Path $PlanDir ("{0}_robocopy_commands.txt" -f $today)
  $copyCmds | Set-Content -LiteralPath $cmdFile -Encoding UTF8
  Say ''
  Say "plan written: $planFile"
  Say "robocopy commands: $cmdFile"
  if ($Apply) {
    $rb = Join-Path $newRoot '_log\rollback_note.md'
    $note = "# Rollback note, $today`r`n`r`nCopied from $oldRoot to $newRoot. The old root is untouched and stays read-only until $($rm.rollbackUntil). To roll back: point every tool back at $oldRoot and delete $newRoot. Nothing in the old root was deleted or changed by the restructure script.`r`n"
    if (New-FileIfMissing $rb $note) { Say "create $rb" }
    Say ''
    Say 'NEXT: run Invoke-SiteofficeRewrite.ps1 with the inventory CSV and this map, dry run first.'
  } else {
    Say ''
    Say 'Dry run only. Nothing changed. Re-run with -Apply after reading the plan.'
  }
  exit 0
}
catch {
  try { if ($PlanDir) { $plan | Set-Content -LiteralPath (Join-Path $PlanDir ("{0}_restructure_failed.txt" -f $today)) -Encoding UTF8 } } catch { }
  Write-Host ("RESTRUCTURE FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
