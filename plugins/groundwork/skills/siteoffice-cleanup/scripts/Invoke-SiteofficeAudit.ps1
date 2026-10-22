#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, staleness audit. Read-only. Lists every folder under a root (to a depth)
  with file count, size, newest write date, git state, and flags that suggest cleanup, so the
  user can decide folder by folder what moves, what is archived and what is deleted.

.DESCRIPTION
  Per folder: files, MB, newest and oldest write, days since the newest write, git (last commit,
  dirty files, unpushed commits, remote), and flags: Empty, Stale (no write in -StaleDays),
  Junk (caches and build output), Worktree, Reports (many dated report files), NoGitDirty
  (not a repo, so nothing protects it). Root-level loose files are one extra row. Nothing is
  changed or deleted. One CSV out plus a summary.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeAudit.ps1 -Root 'C:\old-root' -Depth 2 -StaleDays 60 -OutCsv 'C:\old-root\hub\04_engineering\migration\2026-01-31_folder_audit.csv'
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [int]$Depth = 2,
  [int]$StaleDays = 60,
  [string]$OutCsv = '',
  [string[]]$Skip = @('.git'),
  [string[]]$JunkNames = @('node_modules','__pycache__','.pytest_cache','.venv','venv','dist','build','.mypy_cache','.ruff_cache','.next','out','coverage','.cache','.tox')
)

$ErrorActionPreference = 'Stop'
$rows = New-Object System.Collections.Generic.List[object]
$now = Get-Date

function Measure-Folder {
  param([string]$Path)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $files = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -notlike '*\.git\*' }
  } finally { $ErrorActionPreference = $prev }
  $n = 0; $bytes = 0; $newest = $null; $oldest = $null; $reports = 0
  foreach ($f in $files) {
    $n++; $bytes += $f.Length
    if ($null -eq $newest -or $f.LastWriteTime -gt $newest) { $newest = $f.LastWriteTime }
    if ($null -eq $oldest -or $f.LastWriteTime -lt $oldest) { $oldest = $f.LastWriteTime }
    if ($f.Name -match '(?i)report|export|snapshot' -and $f.Name -match '\d{8}') { $reports++ }
  }
  return [pscustomobject]@{ Files = $n; MB = [math]::Round($bytes / 1MB, 1); Newest = $newest; Oldest = $oldest; Reports = $reports }
}

function Get-GitState {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath (Join-Path $Path '.git'))) { return $null }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $last = (& git -C $Path log -1 --format=%ad --date=short 2>$null)
    $dirty = @(& git -C $Path status --porcelain 2>$null).Count
    $ahead = (& git -C $Path rev-list --count '@{u}..HEAD' 2>$null)
    if (-not $ahead) { $ahead = 'no upstream' }
    $remote = (& git -C $Path remote get-url origin 2>$null)
    if (-not $remote) { $remote = 'no remote' }
  } finally { $ErrorActionPreference = $prev }
  return [pscustomobject]@{ LastCommit = $last; Dirty = $dirty; Unpushed = $ahead; Remote = $remote }
}

function Add-FolderRow {
  param([IO.DirectoryInfo]$Dir, [int]$Level)
  $m = Measure-Folder $Dir.FullName
  $g = Get-GitState $Dir.FullName
  $days = if ($m.Newest) { [int]($now - $m.Newest).TotalDays } else { -1 }
  $flags = @()
  if ($m.Files -eq 0) { $flags += 'Empty' }
  elseif ($days -ge $StaleDays) { $flags += "Stale($days d)" }
  if ($JunkNames -contains $Dir.Name) { $flags += 'Junk' }
  if ($Dir.Name -eq '.worktrees' -or $Dir.FullName -like '*\.worktrees\*') { $flags += 'Worktree' }
  if ($m.Reports -ge 5) { $flags += "Reports($($m.Reports))" }
  if ($Dir.Name -match '(?i)^(_?archive|old|backup|bak|tmp|temp|scratch|export|extra|misc|delete)') { $flags += 'NameSaysTemporary' }
  if ($null -eq $g -and $m.Files -gt 0 -and $Level -eq 1) { $flags += 'NoGit' }
  if ($g -and $g.Dirty -gt 0) { $flags += "Dirty($($g.Dirty))" }
  if ($g -and $g.Unpushed -ne '0' -and $g.Unpushed -ne 'no upstream') { $flags += "Unpushed($($g.Unpushed))" }
  if ($g -and $g.Unpushed -eq 'no upstream') { $flags += 'NoUpstream' }
  if ($Dir.Attributes -band [IO.FileAttributes]::ReparsePoint) { $flags += 'Link' }
  $rows.Add([pscustomobject]@{
    Level      = $Level
    Path       = $Dir.FullName
    Files      = $m.Files
    MB         = $m.MB
    Newest     = $(if ($m.Newest) { $m.Newest.ToString('yyyy-MM-dd') } else { '' })
    DaysIdle   = $days
    Git        = $(if ($g) { 'yes' } else { '' })
    LastCommit = $(if ($g) { $g.LastCommit } else { '' })
    Dirty      = $(if ($g) { $g.Dirty } else { '' })
    Unpushed   = $(if ($g) { $g.Unpushed } else { '' })
    Remote     = $(if ($g) { $g.Remote } else { '' })
    Flags      = ($flags -join ' ')
    Decision   = ''
  })
}

function Walk {
  param([string]$Path, [int]$Level)
  if ($Level -gt $Depth) { return }
  $dirs = Get-ChildItem -LiteralPath $Path -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $Skip -notcontains $_.Name }
  foreach ($d in $dirs) {
    Add-FolderRow -Dir $d -Level $Level
    Walk -Path $d.FullName -Level ($Level + 1)
  }
}

try {
  $Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
  if ([string]::IsNullOrWhiteSpace($OutCsv)) { $OutCsv = Join-Path $env:TEMP ("siteoffice_audit_{0}.csv" -f (Get-Date -Format 'yyyy-MM-dd')) }
  $outDir = Split-Path -Parent $OutCsv
  if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
  Write-Host "Auditing $Root to depth $Depth, stale after $StaleDays days ..."

  # root-level loose files as one row
  $loose = @(Get-ChildItem -LiteralPath $Root -File -Force -ErrorAction SilentlyContinue)
  if ($loose.Count -gt 0) {
    $newest = ($loose | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    $rows.Add([pscustomobject]@{ Level = 0; Path = "$Root\(loose files)"; Files = $loose.Count; MB = [math]::Round((($loose | Measure-Object Length -Sum).Sum) / 1MB, 1); Newest = $newest.ToString('yyyy-MM-dd'); DaysIdle = [int]($now - $newest).TotalDays; Git = ''; LastCommit = ''; Dirty = ''; Unpushed = ''; Remote = ''; Flags = (($loose | ForEach-Object { $_.Name }) -join ' '); Decision = '' })
  }
  Walk -Path $Root -Level 1

  $rows | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8
  Write-Host ''
  Write-Host ("{0} folder rows. Level-1 overview:" -f $rows.Count)
  $rows | Where-Object Level -le 1 | Sort-Object MB -Descending | ForEach-Object {
    Write-Host ("  {0,7} MB {1,6} files  idle {2,4} d  {3,-42} {4}" -f $_.MB, $_.Files, $_.DaysIdle, (Split-Path -Leaf $_.Path), $_.Flags)
  }
  Write-Host ''
  Write-Host ("flagged Empty: {0}   Stale: {1}   Junk: {2}   Worktree: {3}   NameSaysTemporary: {4}   Dirty: {5}   Unpushed: {6}" -f `
    @($rows | Where-Object Flags -like '*Empty*').Count, @($rows | Where-Object Flags -like '*Stale*').Count, @($rows | Where-Object Flags -like '*Junk*').Count, `
    @($rows | Where-Object Flags -like '*Worktree*').Count, @($rows | Where-Object Flags -like '*NameSaysTemporary*').Count, @($rows | Where-Object Flags -like '*Dirty*').Count, @($rows | Where-Object Flags -like '*Unpushed*').Count)
  Write-Host "CSV: $OutCsv   (fill the Decision column: keep, archive, delete, ask)"
  exit 0
}
catch {
  Write-Host ("AUDIT FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
