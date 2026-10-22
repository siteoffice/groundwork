#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, per-user tool state that a text rewrite of the tree never reaches:
  Claude Desktop's claude_desktop_config.json, Cowork's spaces.json, and .lnk shortcuts.
  Dry run by default. Refuses to apply while Claude Desktop runs.

.DESCRIPTION
  Follows the migration map: every string that names the old root (backslash, forward slash or
  WSL spelling) is remapped to the new root, with the first path segments remapped when a map
  entry moved that folder elsewhere, and staysSubfolders left alone.
  - claude_desktop_config.json: MCP server args and filesystem allow-lists. Every string value.
  - spaces.json (Cowork local projects): folders[].path, instructions, description. Ids are kept,
    so memory, outputs and scheduled tasks of each project survive. The UI cannot detach a
    connected folder; this file is the only way.
  - shortcuts: TargetPath, Arguments and WorkingDirectory of every .lnk in the Desktop and Start
    Menu folders, via WScript.Shell. .lnk files are binary; no text scan sees them.
  Each written file gets a timestamped .bak next to it. After apply the script lists every
  path in the two JSON files that does not exist on disk, so dead entries can be pruned by hand.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop, Claude Desktop QUIT (File > Quit, the tray icon too), dry run then apply:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeToolStateRewrite.ps1 -Map 'C:\siteoffice\acme\10_holding\04_engineering\migration\2026-01-31_migration_map.json'
    .\Invoke-SiteofficeToolStateRewrite.ps1 -Map '<same>' -Apply
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Map,
  [switch]$Apply,
  [string[]]$ClaudeDesktopDirs = @("$env:LOCALAPPDATA\Packages", "$env:APPDATA\Claude"),
  [string[]]$ShortcutDirs = @("$env:USERPROFILE\Desktop", "$env:APPDATA\Microsoft\Windows\Start Menu", "$env:ProgramData\Microsoft\Windows\Start Menu"),
  [switch]$SkipShortcuts,
  [switch]$SkipClaudeDesktop
)
$ErrorActionPreference = 'Stop'
try {
  $m = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Map).Path -Raw -Encoding UTF8 | ConvertFrom-Json
  $oldRoot = $m.oldRoot.TrimEnd('\'); $newRoot = $m.newRoot.TrimEnd('\')
  $stays = @($m.staysSubfolders)

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

  $drive = $oldRoot.Substring(0,1).ToLower(); $tailFwd = $oldRoot.Substring(3) -replace '\\','/'
  $spell = [ordered]@{ bs = $oldRoot; fs = ($oldRoot -replace '\\','/'); wsl = ("/mnt/{0}/{1}" -f $drive, $tailFwd) }
  $ndrive = $newRoot.Substring(0,1).ToLower(); $ntailFwd = $newRoot.Substring(3) -replace '\\','/'
  $nspell = [ordered]@{ bs = $newRoot; fs = ($newRoot -replace '\\','/'); wsl = ("/mnt/{0}/{1}" -f $ndrive, $ntailFwd) }
  $nameChar = '[A-Za-z0-9_\-\.~]'
  $alts = ($spell.Values | ForEach-Object { [regex]::Escape($_) }) -join '|'
  $rx = New-Object System.Text.RegularExpressions.Regex ("(?i)(?<root>$alts)(?<rest>(?:[\\/]+$nameChar+)*)(?!$nameChar)")

  function Convert-Text([string]$Text) {
    return $rx.Replace($Text, {
      param($match)
      $style = 'bs'
      foreach ($k in $spell.Keys) { if ($spell[$k] -ieq $match.Groups['root'].Value) { $style = $k; break } }
      $relBs = (($match.Groups['rest'].Value -replace '/', '\') -replace '\\\\', '\').TrimStart('\')
      $newRel = Resolve-NewRel $relBs
      if ($null -eq $newRel) { return $match.Value }
      if ($newRel -eq '') { return $nspell[$style] }
      $sep = if ($style -eq 'bs') { '\' } else { '/' }
      $relOut = if ($style -eq 'bs') { $newRel } else { $newRel -replace '\\','/' }
      return $nspell[$style] + $sep + $relOut
    })
  }

  $script:changes = 0
  function Walk($node) {
    if ($node -is [string]) {
      if ($rx.IsMatch($node)) { $new = Convert-Text $node; if ($new -ne $node) { $script:changes++; Write-Host ("   {0}`n     -> {1}" -f $node.Substring(0, [Math]::Min(110, $node.Length)), $new.Substring(0, [Math]::Min(110, $new.Length))) }; return $new }
      return $node
    }
    if ($node -is [System.Management.Automation.PSCustomObject]) {
      foreach ($p in $node.PSObject.Properties) { $p.Value = Walk $p.Value }
      return $node
    }
    if ($node -is [System.Collections.IList]) {
      $out = @(); foreach ($item in $node) { $out += ,(Walk $item) }
      return ,$out
    }
    return $node
  }

  $mode = if ($Apply) { 'APPLY' } else { 'DRY RUN' }
  Write-Host "== Siteoffice tool state rewrite, $mode"
  Write-Host "old: $oldRoot   new: $newRoot"

  # Claude Desktop must be closed: it rewrites both stores on exit. Name alone is not enough,
  # Claude Code also runs as a process called claude; the Path tells them apart.
  $desktop = @(Get-Process -Name Claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like '*\Claude.exe' })
  if ($desktop.Count -gt 0 -and $Apply -and -not $SkipClaudeDesktop) {
    throw "Claude Desktop is running ($($desktop.Count) process(es), $($desktop[0].Path)). Quit it (tray icon too), then re-run."
  }

  # --- 1. Claude Desktop and Cowork stores ---------------------------------------
  if (-not $SkipClaudeDesktop) {
    $storeFiles = @()
    foreach ($d in $ClaudeDesktopDirs) {
      if (-not (Test-Path -LiteralPath $d)) { continue }
      if ($d -like '*\Packages') {
        Get-ChildItem -LiteralPath $d -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue | ForEach-Object {
          $lc = Join-Path $_.FullName 'LocalCache\Roaming\Claude'
          if (Test-Path -LiteralPath $lc) { $storeFiles += Get-ChildItem -LiteralPath $lc -Recurse -File -Include 'claude_desktop_config.json','spaces.json' -ErrorAction SilentlyContinue }
        }
      } else {
        $storeFiles += Get-ChildItem -LiteralPath $d -Recurse -File -Include 'claude_desktop_config.json','spaces.json' -ErrorAction SilentlyContinue
      }
    }
    $seen = @{}
    foreach ($f in $storeFiles) {
      $real = $f.FullName
      try { $real = (Get-Item -LiteralPath $f.FullName).FullName } catch { }
      if ($seen.ContainsKey($real.ToLower())) { continue }; $seen[$real.ToLower()] = 1
      Write-Host "`n-- $real"
      $raw = Get-Content -LiteralPath $real -Raw -Encoding UTF8
      if (-not $rx.IsMatch($raw)) { Write-Host '   no old-root path'; continue }
      $doc = $raw | ConvertFrom-Json
      $before = $script:changes
      $doc = Walk $doc
      $n = $script:changes - $before
      Write-Host "   $n string(s) to change"
      if ($Apply -and $n -gt 0) {
        $out = $doc | ConvertTo-Json -Depth 100
        $null = $out | ConvertFrom-Json   # throws on invalid JSON before anything is written
        Copy-Item -LiteralPath $real -Destination "$real.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Set-Content -LiteralPath $real -Value $out -Encoding UTF8
        Write-Host '   written (backup next to it)'
        Write-Host '   paths in this file that do not exist on disk:'
        [regex]::Matches($out, '"([A-Za-z]:\\\\[^"]+)"') | ForEach-Object { $_.Groups[1].Value.Replace('\\', '\') } | Sort-Object -Unique |
          Where-Object { -not (Test-Path -LiteralPath $_) } | ForEach-Object { Write-Host "     MISSING $_" }
      }
    }
  }

  # --- 2. shortcuts ---------------------------------------------------------------
  if (-not $SkipShortcuts) {
    Write-Host "`n-- shortcuts"
    $wsh = New-Object -ComObject WScript.Shell
    $lnkCount = 0
    foreach ($d in $ShortcutDirs) {
      if (-not (Test-Path -LiteralPath $d)) { continue }
      foreach ($f in (Get-ChildItem -LiteralPath $d -Recurse -File -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
        $lnk = $wsh.CreateShortcut($f.FullName)
        $t = "$($lnk.TargetPath)|$($lnk.Arguments)|$($lnk.WorkingDirectory)"
        if (-not $rx.IsMatch($t)) { continue }
        $lnkCount++
        Write-Host "   $($f.FullName)"
        $nt = Convert-Text $lnk.TargetPath; $na = Convert-Text $lnk.Arguments; $nw = Convert-Text $lnk.WorkingDirectory
        Write-Host "     target: $($lnk.TargetPath) -> $nt"
        Write-Host "     args:   $($lnk.Arguments) -> $na"
        Write-Host "     cwd:    $($lnk.WorkingDirectory) -> $nw"
        if ($Apply) {
          Copy-Item -LiteralPath $f.FullName -Destination "$($f.FullName).bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
          $lnk.TargetPath = $nt; $lnk.Arguments = $na; $lnk.WorkingDirectory = $nw; $lnk.Save()
          Write-Host '     written'
        }
      }
    }
    Write-Host "   shortcuts holding the old root: $lnkCount"
  }

  Write-Host ''
  if (-not $Apply) { Write-Host 'Dry run only. Nothing changed. Quit Claude Desktop, then re-run with -Apply.' }
  exit 0
}
catch {
  Write-Host ("TOOL STATE REWRITE FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
