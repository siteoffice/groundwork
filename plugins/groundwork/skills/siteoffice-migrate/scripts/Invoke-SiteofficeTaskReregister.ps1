#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, Windows Task Scheduler. Points every task whose name matches a prefix at
  the new root, following the migration map, and proves each one. Dry run by default.
  Apply needs an ELEVATED PowerShell (Set-ScheduledTask on RunLevel Highest tasks).

.DESCRIPTION
  1. Exports every matching task definition as XML to -PlanDir\tasks_before_<date>\ (rollback).
  2. Rewrites Execute, Arguments and WorkingDirectory of every action that names the old root,
     in place, with Set-ScheduledTask. Tasks whose actions go through a shim (.vbs, .bat) that
     itself holds the path are listed: re-run their registration script with -RegisterScripts,
     the script rebuilds the shim at the new root.
  3. Enables the tasks, starts the ones named in -StartNow, waits, prints State, LastRunTime and
     LastTaskResult for each. A task is migrated when LastTaskResult is 0 AND its output exists
     on disk (regenerated files under the new root are the cheapest proof); read both.
  A transcript lands in -PlanDir\tasks_reregister_<date>.txt.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  Elevated PowerShell on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeTaskReregister.ps1 -Map 'C:\siteoffice\acme\10_holding\04_engineering\migration\2026-01-31_migration_map.json' -TaskPrefix 'ACME-' -PlanDir 'C:\siteoffice\acme\10_holding\04_engineering\migration\plans'
    .\Invoke-SiteofficeTaskReregister.ps1 -Map '<same>' -TaskPrefix 'ACME-' -PlanDir '<same>' -RegisterScripts 'C:\siteoffice\acme\10_holding\04_engineering\backup\register_backup_task.ps1' -StartNow 'ACME-OS-notify' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Map,
  [Parameter(Mandatory = $true)][string]$TaskPrefix,
  [string]$PlanDir = '',
  [string[]]$RegisterScripts = @(),
  [string[]]$StartNow = @(),
  [int]$WaitSeconds = 90,
  [switch]$Apply
)
$ErrorActionPreference = 'Continue'
try {
  $m = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Map).Path -Raw -Encoding UTF8 | ConvertFrom-Json
  $oldRoot = $m.oldRoot.TrimEnd('\'); $newRoot = $m.newRoot.TrimEnd('\')
  $stays = @($m.staysSubfolders)
  if ([string]::IsNullOrWhiteSpace($PlanDir)) { $PlanDir = Join-Path $env:TEMP 'siteoffice-migrate' }
  if (-not (Test-Path -LiteralPath $PlanDir)) { New-Item -ItemType Directory -Path $PlanDir -Force | Out-Null }
  $stamp = Get-Date -Format 'yyyy-MM-dd'

  $remaps = @()
  foreach ($e in $m.entries) {
    if ([string]::IsNullOrEmpty($e.old) -or [string]::IsNullOrEmpty($e.new)) { continue }
    if ($e.type -in 'stays', 'delete') { continue }
    $relOld = $e.old
    if ([IO.Path]::IsPathRooted($relOld)) { $relOld = $relOld.Substring($oldRoot.Length).TrimStart('\') }
    $remaps += [pscustomobject]@{ old = $relOld.TrimEnd('\'); new = $e.new.TrimEnd('\') }
  }
  $remaps = $remaps | Sort-Object { $_.old.Length } -Descending
  function Resolve-NewRel([string]$Rel) {
    foreach ($s in $stays) { if ($s -and ($Rel -eq $s -or $Rel.StartsWith($s + '\', 'OrdinalIgnoreCase'))) { return $null } }
    foreach ($r in $remaps) {
      if ($Rel -eq $r.old) { return $r.new }
      if ($Rel.StartsWith($r.old + '\', 'OrdinalIgnoreCase')) { return ($r.new + $Rel.Substring($r.old.Length)) }
    }
    return $Rel
  }
  $nameChar = '[A-Za-z0-9_\-\.~]'
  $rx = New-Object System.Text.RegularExpressions.Regex ("(?i)(?<root>" + [regex]::Escape($oldRoot) + ")(?<rest>(?:\\+$nameChar+)*)(?!$nameChar)")
  function Convert-Text([string]$Text) {
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    return $rx.Replace($Text, {
      param($match)
      $relBs = $match.Groups['rest'].Value.TrimStart('\')
      $newRel = Resolve-NewRel $relBs
      if ($null -eq $newRel) { return $match.Value }
      if ($newRel -eq '') { return $newRoot }
      return "$newRoot\$newRel"
    })
  }

  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
  Start-Transcript -Path (Join-Path $PlanDir "tasks_reregister_$stamp.txt") -Append | Out-Null
  Write-Host "== Siteoffice task re-registration, $mode, elevated: $elevated, $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
  if ($Apply -and -not $elevated) { throw 'Apply needs an elevated PowerShell (Run as administrator).' }

  $tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -like "$TaskPrefix*" })
  Write-Host "tasks matching '$TaskPrefix*': $($tasks.Count)"

  # 1. rollback export
  $before = Join-Path $PlanDir "tasks_before_$stamp"
  if ($Apply) { New-Item -ItemType Directory -Path $before -Force | Out-Null }
  foreach ($t in $tasks) {
    if ($Apply) { Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -LiteralPath (Join-Path $before "$($t.TaskName).xml") -Encoding Unicode }
    foreach ($a in $t.Actions) { Write-Host ("BEFORE {0,-28} {1} {2}" -f $t.TaskName, $a.Execute, $a.Arguments) }
  }

  # 2. rewrite actions in place
  $shims = @()
  foreach ($t in $tasks) {
    $touched = $false
    $newActions = foreach ($a in $t.Actions) {
      $exe = Convert-Text $a.Execute; $arg = Convert-Text $a.Arguments; $wd = Convert-Text $a.WorkingDirectory
      if ($exe -ne $a.Execute -or $arg -ne $a.Arguments -or $wd -ne $a.WorkingDirectory) { $touched = $true }
      foreach ($piece in @($exe, $arg)) { if ($piece -match '\.(vbs|bat|cmd)\b') { $shims += "$($t.TaskName): $piece" } }
      if ($wd) { New-ScheduledTaskAction -Execute $exe -Argument $arg -WorkingDirectory $wd } else { New-ScheduledTaskAction -Execute $exe -Argument $arg }
    }
    if (-not $touched) { Write-Host ("KEEP   {0,-28} no old-root path in its actions" -f $t.TaskName); continue }
    foreach ($na in $newActions) { Write-Host ("AFTER  {0,-28} {1} {2}" -f $t.TaskName, $na.Execute, $na.Arguments) }
    if ($Apply) { Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Action $newActions | Out-Null; Write-Host ("SET    {0}" -f $t.TaskName) }
  }
  if ($shims.Count -gt 0) {
    Write-Host "`nActions that go through a shim; the shim file itself may still hold the old root. Re-run the registration script that writes it (-RegisterScripts), or rewrite the shim:"
    $shims | Sort-Object -Unique | ForEach-Object { Write-Host "   $_" }
  }

  # 3. registration scripts at the new root
  foreach ($s in $RegisterScripts) {
    Write-Host "`n--- $s"
    if (-not (Test-Path -LiteralPath $s)) { Write-Host 'MISSING script'; continue }
    if (-not $Apply) { Write-Host 'would run'; continue }
    Push-Location (Split-Path $s)
    try { & $s } catch { Write-Host "FAILED: $($_.Exception.Message)" }
    Pop-Location
  }

  # 4. enable, start, prove
  if ($Apply) {
    $tasks | Enable-ScheduledTask | Out-Null
    foreach ($n in $StartNow) { Start-ScheduledTask -TaskName $n; Write-Host "started $n" }
    if ($StartNow.Count -gt 0) { Start-Sleep -Seconds $WaitSeconds }
    Write-Host ''
    foreach ($t in (Get-ScheduledTask | Where-Object { $_.TaskName -like "$TaskPrefix*" })) {
      $i = $t | Get-ScheduledTaskInfo
      Write-Host ("RESULT {0,-28} {1,-9} last={2:yyyy-MM-dd HH:mm} result={3}" -f $t.TaskName, $t.State, $i.LastRunTime, $i.LastTaskResult)
      foreach ($a in $t.Actions) { Write-Host ("       {0} {1}" -f $a.Execute, $a.Arguments) }
    }
    Write-Host "`nresult 0 is half the proof; the other half is the task's output file under the new root with a fresh timestamp."
  } else {
    Write-Host "`nDry run only. Nothing changed. Re-run elevated with -Apply."
  }
  Stop-Transcript | Out-Null
  Write-Host "transcript: $PlanDir\tasks_reregister_$stamp.txt"
  exit 0
}
catch {
  Write-Host ("TASK REREGISTER FAILED: {0}" -f $_.Exception.Message)
  try { Stop-Transcript | Out-Null } catch { }
  exit 1
}
