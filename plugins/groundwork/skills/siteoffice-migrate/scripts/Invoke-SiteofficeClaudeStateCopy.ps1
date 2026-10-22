#Requires -Version 7.0
<#
.SYNOPSIS
  Siteoffice migrate, Claude Code per-path state. Copies (never moves) the state Claude Code
  keys by folder path from the old root to the new root, following the migration map.
  Dry run by default. PowerShell 7 (pwsh): ConvertFrom-Json -AsHashtable keeps ~\.claude.json intact.

.DESCRIPTION
  Claude Code state is two things:
  - ~\.claude.json  projects[<path>]: approvals, allowed tools, per-project settings. For every
    key under the old root with a mapped new path, the same entry is added under the new key.
    Existing new keys are left alone.
  - ~\.claude\projects\<encoded path>: sessions and memory. Each folder under the old root's
    encoded prefix is copied (robocopy /E) to its new encoded name; existing targets are skipped.
  Worktree folders (name contains --worktrees-) and staysSubfolders are skipped on purpose.
  Old entries and folders stay for the rollback window. Close every Claude Code window before
  -Apply: Claude Code rewrites ~\.claude.json on exit and would overwrite the change.

.EXAMPLE
  pwsh on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeClaudeStateCopy.ps1 -Map 'C:\siteoffice\acme\10_holding\04_engineering\migration\2026-01-31_migration_map.json'
    .\Invoke-SiteofficeClaudeStateCopy.ps1 -Map '<same>' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Map,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
try {
  $m = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Map).Path -Raw -Encoding UTF8 | ConvertFrom-Json
  $oldRoot = $m.oldRoot.TrimEnd('\'); $newRoot = $m.newRoot.TrimEnd('\')
  $stays = @($m.staysSubfolders)

  # old relative -> new relative, longest first; '' -> '' handles the root itself
  $rules = @([pscustomobject]@{ old = ''; new = '' })
  foreach ($e in $m.entries) {
    if ([string]::IsNullOrEmpty($e.old) -or [string]::IsNullOrEmpty($e.new)) { continue }
    if ($e.type -in 'stays', 'delete', 'generated') { continue }
    $relOld = $e.old
    if ([IO.Path]::IsPathRooted($relOld)) { $relOld = $relOld.Substring($oldRoot.Length).TrimStart('\') }
    $rules += [pscustomobject]@{ old = $relOld; new = $e.new }
  }
  $rules = $rules | Sort-Object { $_.old.Length } -Descending

  function Map-Path([string] $p) {
    # new path with backslashes, or $null when not under oldRoot, in a stays subtree, or unmapped
    $n = $p -replace '/', '\'
    if (-not $n.StartsWith($oldRoot, 'OrdinalIgnoreCase')) { return $null }
    $rel = $n.Substring($oldRoot.Length).TrimStart('\')
    foreach ($s in $stays) { if ($s -and ($rel -eq $s -or $rel -like "$s\*")) { return $null } }
    foreach ($r in $rules) {
      if ($r.old -eq '') { if ($rel -eq '') { return $newRoot } else { continue } }
      if ($rel -eq $r.old) { return "$newRoot\$($r.new)" }
      if ($rel -like "$($r.old)\*") { return "$newRoot\$($r.new)" + $rel.Substring($r.old.Length) }
    }
    return $null
  }
  function Encode-ProjectDir([string] $p) { return ($p -replace '[^A-Za-z0-9]', '-') }

  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  Write-Host "== Claude Code state copy, $mode"

  # 1. ~\.claude.json
  $cfgPath = Join-Path $env:USERPROFILE '.claude.json'
  $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable -Depth 100
  $added = 0; $skipped = 0
  $keys = @($cfg.projects.Keys)
  foreach ($k in $keys) {
    $new = Map-Path $k
    if (-not $new) { continue }
    $newKey = if ($k -match '/') { $new -replace '\\', '/' } else { $new }
    if ($cfg.projects.ContainsKey($newKey)) { Write-Host ("skip   {0,-45} -> {1} (exists)" -f $k, $newKey); $skipped++; continue }
    Write-Host ("entry  {0,-45} -> {1}" -f $k, $newKey)
    if ($Apply) { $cfg.projects[$newKey] = $cfg.projects[$k] }
    $added++
  }
  if ($Apply -and $added -gt 0) {
    $bak = "$cfgPath.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item -LiteralPath $cfgPath -Destination $bak
    ($cfg | ConvertTo-Json -Depth 100) | Set-Content -LiteralPath $cfgPath -Encoding UTF8 -NoNewline
    Write-Host "written $cfgPath (backup: $bak)"
  }
  Write-Host "projects entries: $added to add, $skipped already present"

  # 2. ~\.claude\projects\<encoded old root>*
  $projRoot = Join-Path $env:USERPROFILE '.claude\projects'
  $oldPrefix = Encode-ProjectDir $oldRoot
  $copied = 0; $skippedDirs = 0
  foreach ($d in Get-ChildItem -LiteralPath $projRoot -Directory | Where-Object { $_.Name -eq $oldPrefix -or $_.Name -like "$oldPrefix-*" }) {
    if ($d.Name -like '*--worktrees-*') { Write-Host ("skip   {0,-70} (worktree)" -f $d.Name); $skippedDirs++; continue }
    # recover the original path: match the encoded name against the known .claude.json keys
    $src = $keys | Where-Object { (Encode-ProjectDir ($_ -replace '/', '\')) -eq $d.Name } | Select-Object -First 1
    if (-not $src) { Write-Host ("skip   {0,-70} (no matching .claude.json key)" -f $d.Name); $skippedDirs++; continue }
    $new = Map-Path $src
    if (-not $new) { Write-Host ("skip   {0,-70} (stays or unmapped)" -f $d.Name); $skippedDirs++; continue }
    $dest = Join-Path $projRoot (Encode-ProjectDir $new)
    if (Test-Path -LiteralPath $dest) { Write-Host ("skip   {0,-70} -> {1} (exists)" -f $d.Name, (Split-Path $dest -Leaf)); $skippedDirs++; continue }
    $n = (Get-ChildItem -LiteralPath $d.FullName -Recurse -File).Count
    Write-Host ("folder {0,-70} -> {1}  ({2} files)" -f $d.Name, (Split-Path $dest -Leaf), $n)
    if ($Apply) { robocopy $d.FullName $dest /E /NFL /NDL /NJH /NJS /NP | Out-Null; if ($LASTEXITCODE -gt 7) { Write-Host "robocopy exit $LASTEXITCODE for $($d.Name)" } }
    $copied++
  }
  Write-Host "project folders: $copied to copy, $skippedDirs skipped"
  if (-not $Apply) { Write-Host 'Dry run only. Nothing changed. Close Claude Code, then re-run with -Apply.' }
  exit 0
}
catch {
  Write-Host ("STATE COPY FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
