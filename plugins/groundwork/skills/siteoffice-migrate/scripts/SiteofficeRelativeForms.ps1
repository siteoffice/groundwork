# Shared helper for Invoke-SiteofficeRenameToRule.ps1 (Mode 6) and Invoke-SiteofficeRewrite.ps1
# (Mode 2). Dot-sourced, defines functions only, runs nothing. PowerShell 5.1 and 7. Pure ASCII.
#
# Relative forms are the path spellings an absolute-path scan never sees: <old>\<name>, ..\..\<old>\<name>,
# <unit>\<old>\<name> (the renamed folder is a later segment), and the forward-slash keys of a .tsv
# ledger. Written with placeholders on purpose: a real-looking example in a comment would be rewritten
# when this skill sits inside the tree being renamed.
# Both callers pass the same two things: the folders that are renamed or moved (Rows, each with an
# absolute Old) and a MapPath scriptblock (absolute old path in, absolute path after the move out,
# $null when it stays). Callers dot-source this file at the top of the script, before anything moves,
# because $PSScriptRoot can sit inside a folder the run renames.

function Read-Utf8Strict {
  # Text and BOM flag, or $null when the file is not valid UTF-8 (a Windows-1252 .bat, for one).
  param([string]$Path)
  $bytes = [IO.File]::ReadAllBytes($Path)
  $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
  try { $text = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes) } catch { return $null }
  if ($hasBom) { $text = $text.TrimStart([char]0xFEFF) }
  return [pscustomobject]@{ Text = $text; HasBom = $hasBom }
}

function Test-UnderOrSame {
  param([string]$Path, [string]$Dir)
  $d = $Dir.TrimEnd('\')
  return ($Path.Equals($d, [StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($d + '\', [StringComparison]::OrdinalIgnoreCase))
}

function Get-RelativePath {
  # Path from directory $From to $To, both absolute with backslashes; $null on different drives.
  param([string]$From, [string]$To)
  $f = @($From.TrimEnd('\') -split '\\'); $t = @($To.TrimEnd('\') -split '\\')
  if (-not $f[0].Equals($t[0], [StringComparison]::OrdinalIgnoreCase)) { return $null }
  $i = 0
  while ($i -lt $f.Count -and $i -lt $t.Count -and $f[$i].Equals($t[$i], [StringComparison]::OrdinalIgnoreCase)) { $i++ }
  $parts = @()
  for ($k = $i; $k -lt $f.Count; $k++) { $parts += '..' }
  for ($k = $i; $k -lt $t.Count; $k++) { $parts += $t[$k] }
  return ($parts -join '\')
}

function Find-RelativeMentions {
  # A candidate is a path-like token with at least one segment that is the name of a renamed folder.
  # It is resolved on the PRE-move disk against the file's own folder and then its ancestors up to
  # $Root (or, with leading ..\, that many levels up). It is kept only when a folder along the way is
  # in $Rows AND the next segment exists there, so prose such as "and/or <old>/programs", a foreign
  # D:\other\<old>\x (the token may not follow a path character), an already-renamed form and a
  # form that resolves outside $Root are left alone. The renamed folder may be any segment, not only
  # the first (<unit>\<old>\<name>). Returns rows with Offset, Length and Replacement, applied after the
  # moves by Invoke-RelativeRewrite. Replacement is empty and Note starts with 'review' when the form
  # cannot keep its shape (the target stays while the file moves, or the file moved away from the
  # folder the form was anchored on): listed, never rewritten.
  param(
    [object[]]$File,          # FileInfo objects at their pre-move location
    [string]$Root,
    [object[]]$Rows,          # renamed or moved folders: objects with an absolute Old
    [scriptblock]$MapPath,
    [string[]]$HistoryFiles = @(),
    $SkippedNonUtf8 = $null   # optional List[string], receives files that match but are not UTF-8
  )
  $out = New-Object System.Collections.Generic.List[object]
  if (-not $Rows -or @($Rows).Count -eq 0 -or -not $File) { return $out }
  $rowSet = @{}
  foreach ($r in $Rows) { $rowSet[$r.Old.TrimEnd('\').ToLower()] = $true }
  $names = @($Rows | ForEach-Object { Split-Path -Leaf $_.Old } | Select-Object -Unique)
  $sepRx = '(?:\\\\|[\\/])'
  $segRx = '[A-Za-z0-9_.\-]+'
  $alt = ($names | ForEach-Object { [regex]::Escape($_) }) -join '|'
  $pattern = "(?i)(?<![A-Za-z0-9_\-.~:\\/])(?<up>(?:\.\.?$sepRx)*)(?<path>(?:$segRx$sepRx)*(?:$alt)(?:$sepRx$segRx)+)"
  $rx = New-Object System.Text.RegularExpressions.Regex ($pattern)
  $Root = $Root.TrimEnd('\')

  foreach ($f in $File) {
    $doc = Read-Utf8Strict $f.FullName
    if ($null -eq $doc) {
      if ($null -ne $SkippedNonUtf8 -and (Select-String -LiteralPath $f.FullName -Pattern $pattern -Quiet -ErrorAction SilentlyContinue)) { $SkippedNonUtf8.Add($f.FullName) }
      continue
    }
    $newF = & $MapPath $f.FullName
    if ($null -eq $newF) { continue }
    $newFolder = Split-Path -Parent $newF
    $text = $doc.Text
    foreach ($m in $rx.Matches($text)) {
      $upText = $m.Groups['up'].Value
      $parts = [regex]::Split($m.Groups['path'].Value, "($sepRx)")        # tokens at even index, separators at odd
      while ($parts[$parts.Count - 1].EndsWith('.')) { $parts[$parts.Count - 1] = $parts[$parts.Count - 1].Substring(0, $parts[$parts.Count - 1].Length - 1) }
      if ($parts[$parts.Count - 1] -eq '') { continue }
      $segs = @(); for ($i = 0; $i -lt $parts.Count; $i += 2) { $segs += $parts[$i] }
      $ups = ([regex]::Matches($upText, '\.\.')).Count
      $bases = New-Object System.Collections.Generic.List[string]
      $cur = $f.DirectoryName
      if ($ups -gt 0) {
        for ($i = 0; $i -lt $ups -and $cur; $i++) { $cur = Split-Path -Parent $cur }
        if ($cur -and (Test-UnderOrSame $cur $Root)) { $bases.Add($cur) }
      } else {
        while ($cur -and (Test-UnderOrSame $cur $Root)) {
          $bases.Add($cur)
          $cur = Split-Path -Parent $cur
        }
      }
      $hit = $null
      foreach ($b in $bases) {
        $acc = $b
        for ($i = 0; $i -lt $segs.Count; $i++) {
          $acc = Join-Path $acc $segs[$i]
          if (-not (Test-Path -LiteralPath $acc)) { break }
          if ($rowSet.ContainsKey($acc.ToLower()) -and ($i + 1) -lt $segs.Count -and (Test-Path -LiteralPath (Join-Path $acc $segs[$i + 1]))) { $hit = $b; break }
        }
        if ($hit) { break }
      }
      if (-not $hit) { continue }

      $oldP = $hit; foreach ($s in $segs) { $oldP = Join-Path $oldP $s }
      $newT = & $MapPath $oldP
      $sep0 = $parts[1]
      $dot = if ($ups -eq 0) { $upText } else { '' }                       # a leading .\ is kept
      $note = if ($HistoryFiles -contains $f.Name) { 'history file; excluded from rewrite' } else { '' }
      $newRel = $null
      if ($null -ne $newT) {
        if ($ups -gt 0 -or $hit.Equals($f.DirectoryName, [StringComparison]::OrdinalIgnoreCase)) {
          # relative to the file itself: recompute from the file's new folder
          $r = Get-RelativePath $newFolder $newT
          if ($r) { $newRel = $(if ($r -match '^\.\.(\\|$)') { '' } else { $dot }) + (($r -split '\\') -join $sep0) }
        } else {
          # anchored on an ancestor (a ledger key relative to the estate root): keep the anchor
          $newB = & $MapPath $hit
          if ($newB -and (Test-UnderOrSame $newFolder $newB) -and (Test-UnderOrSame $newT $newB) -and -not $newT.Equals($newB.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
            $newRel = $dot + (($newT.Substring($newB.TrimEnd('\').Length + 1) -split '\\') -join $sep0)
          }
        }
      }
      $oldRel = $upText + ($parts -join '')
      if ($null -ne $newRel -and $oldRel -ceq $newRel) { continue }
      if ($null -eq $newRel -and $note -eq '') { $note = 'review: the form cannot keep its shape (target stays, or file moved away from its anchor), set by hand' }
      $lineNo = 1 + ([regex]::Matches($text.Substring(0, $m.Index), "`n")).Count
      $lineStart = $text.LastIndexOf("`n", [Math]::Max(0, $m.Index - 1)) + 1
      if ($m.Index -eq 0) { $lineStart = 0 }
      $lineEnd = $text.IndexOf("`n", $m.Index); if ($lineEnd -lt 0) { $lineEnd = $text.Length }
      $ctx = $text.Substring($lineStart, $lineEnd - $lineStart).Trim(); if ($ctx.Length -gt 200) { $ctx = $ctx.Substring(0, 200) + '...' }
      $out.Add([pscustomobject]@{
        Source = 'file-relative'; Path = $f.FullName; Line = $lineNo; Match = $oldRel; Replacement = $newRel
        Offset = $m.Index; Length = $oldRel.Length; Context = $ctx; Note = $note
      })
    }
  }
  return $out
}

function Invoke-RelativeRewrite {
  # Applies the rows of Find-RelativeMentions (only rows with an empty Note) at the files' FINAL paths,
  # last offset first so earlier offsets stay valid. A row whose text no longer matches at its offset is
  # reported, not forced. Returns one report row per attempted rewrite.
  param([object[]]$RelRows, [scriptblock]$MapPath)
  $report = New-Object System.Collections.Generic.List[object]
  foreach ($g in ($RelRows | Group-Object Path)) {
    $final = & $MapPath $g.Name
    if (-not $final) { $final = $g.Name }
    $doc = if (Test-Path -LiteralPath $final) { Read-Utf8Strict $final } else { $null }
    if ($null -eq $doc) {
      foreach ($h in $g.Group) { $report.Add([pscustomobject]@{ File = $final; Line = $h.Line; Old = $h.Match; New = $h.Replacement; Status = 'skipped: file missing or not UTF-8' }) }
      continue
    }
    $text = $doc.Text
    foreach ($h in ($g.Group | Sort-Object Offset -Descending)) {
      if ($h.Offset + $h.Length -le $text.Length -and $text.Substring($h.Offset, $h.Length) -ceq $h.Match) {
        $text = $text.Remove($h.Offset, $h.Length).Insert($h.Offset, $h.Replacement)
        $report.Add([pscustomobject]@{ File = $final; Line = $h.Line; Old = $h.Match; New = $h.Replacement; Status = 'rewritten' })
      } else {
        $report.Add([pscustomobject]@{ File = $final; Line = $h.Line; Old = $h.Match; New = $h.Replacement; Status = 'skipped: text changed since the scan' })
      }
    }
    [IO.File]::WriteAllText($final, $text, (New-Object System.Text.UTF8Encoding($doc.HasBom)))
  }
  return $report
}
