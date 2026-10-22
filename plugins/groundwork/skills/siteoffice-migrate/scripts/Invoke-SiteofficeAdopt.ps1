#Requires -Version 7.0
<#
.SYNOPSIS
  Siteoffice migrate, Mode 5: adopt an existing folder as a Siteoffice unit in place. Dry run
  by default. Never touches an existing file; only adds what is missing.

.DESCRIPTION
  -Root is an existing folder that is not yet a Siteoffice unit (no siteoffice.json). The script
  writes siteoffice.json (schemaVersion, type, name, parent, children, language, approver,
  "setup": "structure-only"), adds the unit skeleton files it lacks (STATUS.md, DEADLINES.md,
  SESSION_LOG.md, 00_context\brief.md, 02_roadmap\roadmap.md, 03_logs\decisions.md,
  03_logs\_history\, 04_assets\; containers additionally get 01_projects\ and
  03_logs\hub_inbox.md), registers the unit in the parent's children[] list, and adds a one-pager
  stub at <parent>\01_projects\<name>.md if the parent is a container and the one-pager does not
  already exist. -Parent names the parent unit's folder; if omitted, the nearest ancestor of
  -Root that holds a siteoffice.json is used. Existing files are never overwritten; every write
  is New-FileIfMissing style.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop, dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeAdopt.ps1 -Root 'C:\estate\50_projects\loose-folder' -Type project -OutDir 'C:\estate\04_engineering\migration'
    .\Invoke-SiteofficeAdopt.ps1 -Root 'C:\estate\50_projects\loose-folder' -Type project -OutDir 'C:\estate\04_engineering\migration' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [ValidateSet('holding','company','department','project')][string]$Type = 'project',
  [string]$Parent = '',
  [string]$Language = 'en',
  [string]$Approver = '',
  [switch]$Apply,
  [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
$containerTypes = @('holding','company','department')
$script:added = New-Object System.Collections.Generic.List[string]

function Get-Manifest {
  param([string]$Path)
  $p = Join-Path $Path 'siteoffice.json'
  if (-not (Test-Path -LiteralPath $p)) { return $null }
  return (Get-Content -LiteralPath $p -Raw | ConvertFrom-Json)
}

function Find-NearestManifestAncestor {
  param([string]$Path)
  $cur = Split-Path -Parent $Path
  while ($cur -and $cur.Length -ge 3) {
    if (Test-Path -LiteralPath (Join-Path $cur 'siteoffice.json')) { return $cur }
    $parent = Split-Path -Parent $cur
    if ($parent -eq $cur) { break }
    $cur = $parent
  }
  return $null
}

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
  # mirrors Invoke-SiteofficeRestructure.ps1's Add-UnitSkeleton: only adds what is missing.
  param([string]$NodePath, [string]$Type, [string]$Name, [string]$Parent, [string]$Language)
  $today = Get-Date -Format 'yyyy-MM-dd'
  $tbf = "To be filled, $today."
  $hasAgents = Test-Path -LiteralPath (Join-Path $NodePath 'AGENTS.md')
  $hasClaude = Test-Path -LiteralPath (Join-Path $NodePath 'CLAUDE.md')
  if (-not $hasAgents -and -not $hasClaude) {
    if (New-FileIfMissing (Join-Path $NodePath 'AGENTS.md') "# $Name`r`n`r`nInstruction file for this $Type. $tbf`r`n") { $script:added.Add('AGENTS.md') }
    if (New-FileIfMissing (Join-Path $NodePath 'CLAUDE.md') "@AGENTS.md`r`n") { $script:added.Add('CLAUDE.md') }
  }
  $files = [ordered]@{
    'STATUS.md'             = "# $Name, status`r`n`r`nWhat is true now. $tbf`r`n"
    'DEADLINES.md'          = "# $Name, deadlines`r`n`r`nHard dates only, most imminent on top. None known, asked $today.`r`n"
    'SESSION_LOG.md'        = "# $Name, session log`r`n`r`nOne paragraph per work session, newest on top.`r`n"
    '00_context\brief.md'   = "# $Name, brief`r`n`r`nWhat this node is, who owns it, who approves. $tbf`r`n"
    '02_roadmap\roadmap.md' = "# $Name, roadmap`r`n`r`nPriority order lives here and only here. $tbf`r`n"
    '03_logs\decisions.md'  = "# $Name, decisions`r`n`r`nDecisions with their why, newest on top.`r`n"
  }
  foreach ($k in $files.Keys) { if (New-FileIfMissing (Join-Path $NodePath $k) $files[$k]) { $script:added.Add($k) } }
  foreach ($d in @('03_logs\_history','04_assets')) { if (New-DirIfMissing (Join-Path $NodePath $d)) { $script:added.Add($d + '\') } }
  if ($containerTypes -contains $Type) {
    if (New-DirIfMissing (Join-Path $NodePath '01_projects')) { $script:added.Add('01_projects\') }
    if (New-FileIfMissing (Join-Path $NodePath '03_logs\hub_inbox.md') "# $Name, hub inbox`r`n`r`nIncoming briefs from child nodes, newest on top.`r`n") { $script:added.Add('03_logs\hub_inbox.md') }
  }
}

try {
  $Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
  if ($Root.Length -lt 3 -or $Root.Substring(1,2) -ne ':\') { throw "Root must be a drive path like C:\estate\...\folder, got '$Root'" }
  if (-not (Test-Path -LiteralPath $Root -PathType Container)) { throw "Root is not a folder: $Root" }
  $existing = Get-Manifest -Path $Root
  $name = Split-Path -Leaf $Root
  $stamp = Get-Date -Format 'yyyy-MM-dd'
  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }

  if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $est = Find-NearestManifestAncestor -Path $Root
    $OutDir = if ($est) { Join-Path $est '10_holding\04_engineering\migration' } else { Join-Path $Root '04_engineering\migration' }
  }
  if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

  Write-Host "== Siteoffice adopt, $mode, $stamp"
  Write-Host "Root: $Root  (name: $name, type: $Type)"

  if ($existing) {
    Write-Host ''
    Write-Host "Already a Siteoffice unit (siteoffice.json exists, name='$($existing.name)', type='$($existing.type)'). Nothing to adopt."
    Write-Host 'Use Mode 4 (rename) to change its name, or edit siteoffice.json by hand for other changes.'
    exit 0
  }

  # --- parent -------------------------------------------------------------------
  $parentPath = $null
  if (-not [string]::IsNullOrWhiteSpace($Parent)) {
    $parentPath = if ([IO.Path]::IsPathRooted($Parent)) { $Parent } else { Join-Path (Split-Path -Parent $Root) $Parent }
    $parentPath = (Resolve-Path -LiteralPath $parentPath).Path.TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $parentPath 'siteoffice.json'))) { throw "-Parent does not point at a Siteoffice unit: $parentPath" }
  } else {
    $parentPath = Find-NearestManifestAncestor -Path $Root
  }
  $parentManifest = if ($parentPath) { Get-Manifest -Path $parentPath } else { $null }
  $parentName = if ($parentManifest) { $parentManifest.name } else { '' }
  Write-Host ("Parent: {0}" -f ($(if ($parentPath) { "$parentPath (name: $parentName)" } else { '(none found; this unit will have no parent)' })))
  Write-Host "OutDir: $OutDir"
  Write-Host ''

  # --- plan: skeleton files -------------------------------------------------------
  Write-Host 'Skeleton files this unit is missing (added only if missing):'
  $today = Get-Date -Format 'yyyy-MM-dd'
  $checklist = [ordered]@{
    'STATUS.md'             = 1; 'DEADLINES.md' = 1; 'SESSION_LOG.md' = 1
    '00_context\brief.md'   = 1; '02_roadmap\roadmap.md' = 1; '03_logs\decisions.md' = 1
    '03_logs\_history\'     = 1; '04_assets\' = 1
  }
  if ($containerTypes -contains $Type) { $checklist['01_projects\'] = 1; $checklist['03_logs\hub_inbox.md'] = 1 }
  $hasAgents = Test-Path -LiteralPath (Join-Path $Root 'AGENTS.md')
  $hasClaude = Test-Path -LiteralPath (Join-Path $Root 'CLAUDE.md')
  if (-not $hasAgents -and -not $hasClaude) { $checklist['AGENTS.md'] = 1; $checklist['CLAUDE.md'] = 1 }
  foreach ($k in $checklist.Keys) {
    $p = Join-Path $Root $k.TrimEnd('\')
    $missing = -not (Test-Path -LiteralPath $p)
    Write-Host ("  {0,-24} {1}" -f $k, ($(if ($missing) { '(missing, will add)' } else { '(exists, left alone)' })))
  }
  $onePager = $null
  if ($parentPath -and ($containerTypes -contains ($parentManifest.type))) {
    $onePager = Join-Path $parentPath ("01_projects\{0}.md" -f $name)
    $onePagerExists = Test-Path -LiteralPath $onePager
    Write-Host ("  one-pager stub:          {0}  {1}" -f $onePager, ($(if ($onePagerExists) { '(exists, left alone)' } else { '(missing, will add)' })))
  }
  $childExists = $false
  if ($parentManifest -and $parentManifest.children) {
    foreach ($c in $parentManifest.children) { if ($c.name -eq $name) { $childExists = $true; break } }
  }
  if ($parentPath) {
    Write-Host ("  parent children[] entry: {0}" -f ($(if ($childExists) { '(already registered, left alone)' } else { "(will add: name='$name', type='$Type', path='$($Root.Substring($parentPath.Length + 1))')" })))
  }

  if (-not $Apply) {
    Write-Host ''
    Write-Host 'Dry run only. Nothing changed. Re-run with -Apply after reading the plan above.'
    exit 0
  }

  # --- apply: skeleton -------------------------------------------------------------
  Write-Host ''
  Add-UnitSkeleton -NodePath $Root -Type $Type -Name $name -Parent $parentName -Language $Language
  $manifestPath = Join-Path $Root 'siteoffice.json'
  if (-not (Test-Path -LiteralPath $manifestPath)) {
    $instr = if ($hasClaude -and -not $hasAgents) { 'CLAUDE.md' } else { 'AGENTS.md' }
    $m = [ordered]@{
      schemaVersion   = 1
      type            = $Type
      name            = $name
      parent          = $parentName
      children        = @()
      language        = $Language
      approver        = $Approver
      instructionFile = $instr
      setup           = 'structure-only'
      adoptedOn       = $today
    }
    ($m | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $script:added.Add('siteoffice.json')
  }
  if ($script:added.Count -gt 0) { Write-Host ("Added: {0}" -f ($script:added -join ', ')) } else { Write-Host 'Nothing to add; unit already had every skeleton file.' }

  # --- apply: one-pager -------------------------------------------------------------
  if ($onePager -and -not (Test-Path -LiteralPath $onePager)) {
    $content = "# $name`r`n`r`nAdopted in place, $today. $($Root)`r`n"
    if (New-FileIfMissing $onePager $content) { Write-Host "Added one-pager: $onePager" }
  }

  # --- apply: parent children[] ------------------------------------------------------
  if ($parentPath -and -not $childExists) {
    $pm = Get-Manifest -Path $parentPath
    $relPath = $Root.Substring($parentPath.Length + 1)
    $newChild = [pscustomobject]@{ name = $name; type = $Type; path = $relPath }
    $childrenList = @()
    if ($pm.children) { $childrenList = @($pm.children) }
    $childrenList += $newChild
    $pm.children = $childrenList
    ($pm | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath (Join-Path $parentPath 'siteoffice.json') -Encoding UTF8
    Write-Host "Registered in $parentPath\siteoffice.json children[]: name='$name' type='$Type' path='$relPath'"
  }

  Write-Host ''
  Write-Host 'Done. Hand over to siteoffice-setup Improve mode for the content.'
  exit 0
}
catch {
  Write-Host ("ADOPT FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
