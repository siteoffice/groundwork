#Requires -Version 5.1
<#
.SYNOPSIS
  Siteoffice migrate, inventory mode. Read-only. Finds every hard-coded reference to a root
  folder before that folder moves, so the move can be planned from evidence.

.DESCRIPTION
  Scans a root and a few well-known per-user tool folders for the root path in all its
  spellings: C:\old-root, C:/old-root, C:\\old-root (JSON), /mnt/c/old-root (WSL),
  //c/old-root (git bash). Add your own application-data folders with -ExtraRoots.
  Also lists Windows Task Scheduler actions that mention the root, Claude Code per-path state
  (~\.claude.json project keys and ~\.claude\projects hash folders), and every junction or
  symlink under the root. Writes one CSV. Changes nothing.

  v2 (2026-09-09), after the first real estate, three sources a text scan misses:
  - shortcut: .lnk files on the Desktop and in both Start Menus (binary; read via WScript.Shell)
  - claude-desktop: Claude Desktop's claude_desktop_config.json (MCP server args) and Cowork's
    spaces.json (project folders, instructions) under %LOCALAPPDATA%\Packages\Claude_*\LocalCache
    and %APPDATA%\Claude
  - layout: code that derives paths from the tree's SHAPE rather than naming the root:
    parents[n], Split-Path -Parent chains, Get-ChildItem <root> one level deep, and old folder
    NAMES given with -LayoutNames. A root rewrite leaves these wrong and silent.

  Pure ASCII on purpose: PowerShell 5.1 decodes a BOM-less UTF-8 script as Windows-1252.

.EXAMPLE
  PowerShell on the laptop:
    cd <skill folder>\scripts
    .\Invoke-SiteofficeInventory.ps1 -Root 'C:\old-root' -OutCsv 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_path_inventory.csv'
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Root,
  [string]$OutCsv = '',
  [string[]]$Extensions = @('.ps1','.psm1','.psd1','.json','.toml','.yml','.yaml','.py','.bat','.cmd','.vbs','.js','.sh','.md','.txt','.ini','.cfg','.env','.html','.code-workspace','.tsv'),
  [string[]]$ExcludeDirs = @('.git','.worktrees','node_modules','__pycache__','.venv','venv','dist','build','.raw','tool-results'),
  [string[]]$ExtraRoots = @("$env:USERPROFILE\.claude", "$env:USERPROFILE\.agents", "$env:USERPROFILE\.codex"),
  [string[]]$GeneratedDirs = @('graphify-out'),
  [string[]]$StaysSubfolders = @(),
  [switch]$IncludeArchive,
  [switch]$IncludeGenerated,
  [int]$MaxFileMB = 5,
  [string[]]$LayoutNames = @(),
  [string[]]$ShortcutDirs = @("$env:USERPROFILE\Desktop", "$env:APPDATA\Microsoft\Windows\Start Menu", "$env:ProgramData\Microsoft\Windows\Start Menu"),
  [string[]]$ClaudeDesktopDirs = @("$env:LOCALAPPDATA\Packages", "$env:APPDATA\Claude")
)
# -LayoutNames: folder names that change in the move (e.g. 'hub', 'packs'); scripts that hard-code
#   them next to a relative-path derivation are listed as Source 'layout'.
# -StaysSubfolders: names of direct subfolders of the root that do NOT move (a sibling estate
#   that keeps its path). Their rows are tagged so the rewrite plan skips them.

$ErrorActionPreference = 'Stop'
$rows = New-Object System.Collections.Generic.List[object]

function Add-Row {
  param($Source, $Path, $Line, $Match, $Context, $Note)
  $ctx = if ($null -ne $Context) { ([string]$Context).Trim() } else { '' }
  if ($ctx.Length -gt 220) { $ctx = $ctx.Substring(0, 220) + '...' }
  $rows.Add([pscustomobject]@{
    Source  = $Source
    Path    = $Path
    Line    = $Line
    Match   = $Match
    Context = $ctx
    Note    = $Note
  })
}

try {
  $Root = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
  if ($Root.Length -lt 3 -or $Root.Substring(1,2) -ne ':\') {
    throw "Root must be a drive path like C:\old-root, got '$Root'"
  }
  if (-not $IncludeArchive) { $ExcludeDirs += '_archive' }
  if (-not $IncludeGenerated) { $ExcludeDirs += $GeneratedDirs }
  # Claude Code session transcripts live under ~\.claude\projects\<hash>; only the hash folder
  # names matter (section 3), their contents are history and would drown the CSV.
  $skipLike = @('*\.claude\projects\*')

  function Test-Excluded {
    param([string]$FullPath)
    foreach ($d in $ExcludeDirs) { if ($FullPath -like "*\$d\*") { return $true } }
    foreach ($s in $skipLike) { if ($FullPath -like $s) { return $true } }
    return $false
  }

  $stamp = Get-Date -Format 'yyyy-MM-dd'
  if ([string]::IsNullOrWhiteSpace($OutCsv)) {
    $OutCsv = Join-Path $env:TEMP ("siteoffice_inventory_{0}.csv" -f $stamp)
  }
  $outDir = Split-Path -Parent $OutCsv
  if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
  }

  # --- spellings of the root -------------------------------------------------
  $drive = $Root.Substring(0,1).ToLower()
  $tail  = $Root.Substring(3)                 # after 'C:\'
  $tailFwd = $tail -replace '\\','/'
  $spellings = @(
    $Root,                                     # C:\old-root
    ($Root -replace '\\','/'),                 # C:/old-root
    ($Root -replace '\\','\\'),                  # C:\\old-root (escaped in JSON)
    ("/mnt/{0}/{1}" -f $drive, $tailFwd),      # /mnt/c/old-root (WSL)
    ("//{0}/{1}" -f $drive, $tailFwd)          # //c/old-root (git bash)
  )
  $alts = ($spellings | ForEach-Object { [regex]::Escape($_) }) -join '|'
  # boundary: the root must end there or continue as a path, not as a longer name
  $pattern = "(?i)(?:$alts)(?=[\\/`"'\s\)\]\>,;:]|$)"
  $rx = New-Object System.Text.RegularExpressions.Regex ($pattern)
  $hashForm = ($Root -replace '[:\\]', '-')    # C--old-root, the ~\.claude\projects folder form

  Write-Host "Root:      $Root"
  Write-Host "Spellings: $($spellings -join '  |  ')"
  Write-Host "Output:    $OutCsv"
  Write-Host ''

  # --- 1. files under the root and the extra roots -----------------------------
  $scanRoots = @($Root) + ($ExtraRoots | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
  $extSet = @{}
  foreach ($e in $Extensions) { $extSet[$e.ToLower()] = $true }
  $maxBytes = $MaxFileMB * 1MB
  $fileCount = 0

  foreach ($sr in $scanRoots) {
    Write-Host "Scanning files under $sr ..."
    $files = Get-ChildItem -LiteralPath $sr -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object {
        (-not (Test-Excluded $_.FullName)) -and $extSet.ContainsKey($_.Extension.ToLower()) -and $_.Length -le $maxBytes
      }
    foreach ($f in $files) {
      $fileCount++
      $hits = $null
      $prev = $ErrorActionPreference
      $ErrorActionPreference = 'Continue'
      try { $hits = Select-String -LiteralPath $f.FullName -Pattern $pattern -AllMatches -ErrorAction SilentlyContinue }
      finally { $ErrorActionPreference = $prev }
      if (-not $hits) { continue }
      foreach ($h in $hits) {
        foreach ($m in $h.Matches) {
          $note = ''
          $after = $h.Line.Substring([Math]::Min($h.Line.Length, $m.Index + $m.Length))
          $stays = $false
          foreach ($sf in $StaysSubfolders) {
            if ($after -match ('^[\\/]+' + [regex]::Escape($sf) + '([\\/]|$)')) { $stays = $true; break }
          }
          if ($stays) { $note = 'stays subtree; no rewrite' }
          elseif (($GeneratedDirs | Where-Object { $f.FullName -like "*\$_\*" })) { $note = 'generated; regenerate, do not rewrite' }
          elseif ($f.Extension -ieq '.md') { $note = 'prose path in markdown; rewrite' }
          Add-Row 'file' $f.FullName $h.LineNumber $m.Value $h.Line $note
        }
      }
    }
  }
  Write-Host "  files scanned: $fileCount, file hits: $(@($rows | Where-Object Source -eq 'file').Count)"

  # --- 2. Windows Task Scheduler actions --------------------------------------
  Write-Host 'Reading Task Scheduler actions ...'
  try {
    $tasks = Get-ScheduledTask -ErrorAction Stop
    foreach ($t in $tasks) {
      foreach ($a in $t.Actions) {
        $text = "{0} {1}" -f $a.Execute, $a.Arguments
        if ($a.WorkingDirectory) { $text += " (cwd $($a.WorkingDirectory))" }
        foreach ($m in $rx.Matches($text)) {
          Add-Row 'task' ("{0}{1}" -f $t.TaskPath, $t.TaskName) 0 $m.Value $text 're-register after move; prove by LastTaskResult 0 plus output on disk'
        }
      }
    }
  } catch {
    Add-Row 'task' '(Get-ScheduledTask failed)' 0 '' $_.Exception.Message 'run elevated or check the Schedule service'
  }

  # --- 3. Claude Code per-path state ------------------------------------------
  Write-Host 'Reading Claude Code per-path state ...'
  $claudeJson = Join-Path $env:USERPROFILE '.claude.json'
  if (Test-Path -LiteralPath $claudeJson) {
    try {
      $cfg = Get-Content -LiteralPath $claudeJson -Raw | ConvertFrom-Json
      if ($cfg.projects) {
        foreach ($p in $cfg.projects.PSObject.Properties) {
          if ($rx.IsMatch($p.Name)) {
            $keys = ($p.Value.PSObject.Properties | Select-Object -ExpandProperty Name) -join ','
            Add-Row 'claude-json' $claudeJson 0 $p.Name ("projects['{0}'] keys: {1}" -f $p.Name, $keys) 'approvals and per-project config keyed by path; copy the entry to the new path'
          }
        }
      }
    } catch {
      Add-Row 'claude-json' $claudeJson 0 '' $_.Exception.Message 'could not parse; inspect by hand'
    }
  }
  $projDir = Join-Path $env:USERPROFILE '.claude\projects'
  if (Test-Path -LiteralPath $projDir) {
    Get-ChildItem -LiteralPath $projDir -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like "$hashForm*" } |
      ForEach-Object {
        $n = (Get-ChildItem -LiteralPath $_.FullName -File -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
        Add-Row 'claude-projects' $_.FullName 0 $_.Name ("{0} files (sessions, memory)" -f $n) 'folder name is the path hash; copy or rename to the new hash'
      }
  }

  # --- 3b. Claude Desktop and Cowork stores -----------------------------------
  Write-Host 'Reading Claude Desktop and Cowork stores ...'
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
  foreach ($f in ($storeFiles | Sort-Object FullName -Unique)) {
    $hits = Select-String -LiteralPath $f.FullName -Pattern $pattern -AllMatches -ErrorAction SilentlyContinue
    if (-not $hits) { continue }
    $n = ($hits | ForEach-Object { $_.Matches.Count } | Measure-Object -Sum).Sum
    $what = if ($f.Name -eq 'spaces.json') { 'Cowork projects: folders[].path, instructions, description; rewrite with the app quit, keep the ids' } else { 'Claude Desktop MCP server args and allow-lists; rewrite with the app quit' }
    Add-Row 'claude-desktop' $f.FullName 0 ("{0} hit(s)" -f $n) $what 'Invoke-SiteofficeToolStateRewrite.ps1 covers this file'
  }

  # --- 3c. shortcuts (.lnk are binary, a text scan never sees them) -----------
  Write-Host 'Reading shortcuts ...'
  try {
    $wsh = New-Object -ComObject WScript.Shell
    foreach ($d in $ShortcutDirs) {
      if (-not (Test-Path -LiteralPath $d)) { continue }
      Get-ChildItem -LiteralPath $d -Recurse -File -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
        $lnk = $wsh.CreateShortcut($_.FullName)
        $text = "{0} {1} (cwd {2})" -f $lnk.TargetPath, $lnk.Arguments, $lnk.WorkingDirectory
        if ($rx.IsMatch($text)) { Add-Row 'shortcut' $_.FullName 0 $text $text 'rewrite TargetPath, Arguments and WorkingDirectory via WScript.Shell; Invoke-SiteofficeToolStateRewrite.ps1 covers this' }
      }
    }
  } catch {
    Add-Row 'shortcut' '(WScript.Shell failed)' 0 '' $_.Exception.Message 'list shortcuts by hand'
  }

  # --- 3d. layout assumptions in code ----------------------------------------
  Write-Host 'Scanning scripts for layout assumptions ...'
  # The third pattern is parenthesised on purpose: in an array literal the comma binds tighter
  # than +, so unparenthesised the pieces become separate alternatives and ['"]?\s matches every line.
  $layoutRx = @('parents\[\s*\d+\s*\]', 'Split-Path[^\r\n]*-Parent[^\r\n]*Split-Path', ('Get-ChildItem\s+(?:-LiteralPath\s+|-Path\s+)?[''"]?' + [regex]::Escape($Root) + '[''"]?\s'))
  foreach ($nm in $LayoutNames) { if ($nm) { $layoutRx += ('[''"]' + [regex]::Escape($nm) + '[''"]') } }
  $layoutPattern = '(?i)(?:' + ($layoutRx -join '|') + ')'
  $codeExt = @{ '.py'=1; '.ps1'=1; '.psm1'=1; '.js'=1; '.sh'=1; '.bat'=1; '.cmd'=1 }
  Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { (-not (Test-Excluded $_.FullName)) -and $codeExt.ContainsKey($_.Extension.ToLower()) -and $_.Length -le $maxBytes } |
    ForEach-Object {
      $hits = Select-String -LiteralPath $_.FullName -Pattern $layoutPattern -AllMatches -ErrorAction SilentlyContinue
      foreach ($h in $hits) { Add-Row 'layout' $_.FullName $h.LineNumber ($h.Matches[0].Value) $h.Line 'derives a path from the tree shape or an old folder name; read and fix by hand, a root rewrite does not catch it' }
    }

  # --- 4. junctions and symlinks under the root -------------------------------
  Write-Host 'Listing junctions and symlinks ...'
  Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and (-not (Test-Excluded ($_.FullName + '\'))) } |
    ForEach-Object {
      $target = ''
      try { $target = ($_.Target | Out-String).Trim() } catch { $target = '(unreadable)' }
      Add-Row 'link' $_.FullName 0 $target ("{0} -> {1}" -f $_.Name, $target) 'recreate at the new root; targets inside the root need the new path'
    }

  # --- 5. write and summarise -------------------------------------------------
  $rows | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8

  Write-Host ''
  Write-Host ("Total rows: {0}" -f $rows.Count)
  $rows | Group-Object Source | Sort-Object Count -Descending |
    ForEach-Object { Write-Host ("  {0,-16} {1}" -f $_.Name, $_.Count) }
  Write-Host ''
  Write-Host 'Top 15 files by hit count:'
  $rows | Where-Object Source -eq 'file' | Group-Object Path | Sort-Object Count -Descending |
    Select-Object -First 15 | ForEach-Object { Write-Host ("  {0,5}  {1}" -f $_.Count, $_.Name) }
  Write-Host ''
  Write-Host ("Rows marked stays subtree:        {0}" -f @($rows | Where-Object Note -like 'stays subtree*').Count)
  Write-Host ("Rows marked generated:            {0}" -f @($rows | Where-Object Note -like 'generated*').Count)
  Write-Host ("Layout assumptions (read by hand): {0}" -f @($rows | Where-Object Source -eq 'layout').Count)
  Write-Host ''
  Write-Host "CSV: $OutCsv"
  exit 0
}
catch {
  Write-Host ("INVENTORY FAILED: {0}" -f $_.Exception.Message)
  Write-Host $_.ScriptStackTrace
  exit 1
}
