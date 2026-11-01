<#
.SYNOPSIS
    Semver bump for Groundwork: one version clock across the three manifests.

.DESCRIPTION
    The clock is four fields: plugins[].version for both entries in
    .claude-plugin/marketplace.json, and version in the plugin.json of
    plugins/groundwork and plugins/groundwork-mods. metadata.version in
    marketplace.json is not part of the clock and is left untouched.
    Dry run by default: prints the old and new version and the files it would touch.
    -Apply writes them. The edit is a text replace on the version lines only, so
    formatting and line endings stay as they were. It never runs git; it prints the
    commit and tag commands. When the four fields disagree it lists them and exits 1
    without writing anything.

.EXAMPLE
    pwsh -NoProfile -File .\scripts\bump-version.ps1 -Bump patch
    pwsh -NoProfile -File .\scripts\bump-version.ps1 -Bump minor -Apply
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('patch', 'minor', 'major')][string]$Bump,
    [switch]$Apply,
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
$rx = [regex]'("version"\s*:\s*")([^"]*)(")'
$utf8 = New-Object System.Text.UTF8Encoding($false)

# anchor: in marketplace.json the clock fields are the version lines after "plugins".
$manifests = @(
    @{ rel = '.claude-plugin/marketplace.json'; parts = @('.claude-plugin', 'marketplace.json'); anchor = '"plugins"'; expect = 2 },
    @{ rel = 'plugins/groundwork/.claude-plugin/plugin.json'; parts = @('plugins', 'groundwork', '.claude-plugin', 'plugin.json'); anchor = $null; expect = 1 },
    @{ rel = 'plugins/groundwork-mods/.claude-plugin/plugin.json'; parts = @('plugins', 'groundwork-mods', '.claude-plugin', 'plugin.json'); anchor = $null; expect = 1 }
)

$found = @()
$problems = @()
foreach ($m in $manifests) {
    $path = $Root
    foreach ($p in $m.parts) { $path = Join-Path $path $p }
    $m.path = $path
    if (-not (Test-Path -LiteralPath $path)) { $problems += "missing: $($m.rel)"; continue }
    $text = [IO.File]::ReadAllText($path)
    $m.text = $text
    $start = 0
    if ($m.anchor) {
        $start = $text.IndexOf($m.anchor)
        if ($start -lt 0) { $problems += "no $($m.anchor) in $($m.rel)"; continue }
    }
    $m.start = $start
    $hits = @($rx.Matches($text, $start))
    if (-not $m.anchor) { $hits = @($hits | Select-Object -First 1) }
    if ($hits.Count -ne $m.expect) {
        $problems += "$($m.rel): expected $($m.expect) version field(s), found $($hits.Count)"
    }
    foreach ($h in $hits) { $found += [pscustomobject]@{ file = $m.rel; version = $h.Groups[2].Value } }
}

$distinct = @($found | ForEach-Object { $_.version } | Sort-Object -Unique)
if ($distinct.Count -gt 1) { $problems += 'the version fields disagree' }
if ($problems.Count -gt 0) {
    'REFUSED  the groundwork version clock is not in one state, nothing written'
    foreach ($p in $problems) { "  $p" }
    foreach ($f in $found) { "  $($f.file): $($f.version)" }
    exit 1
}

$current = $distinct[0]
if ($current -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    "REFUSED  the version is not x.y.z: '$current', nothing written"
    exit 1
}
$maj = [int]$Matches[1]; $min = [int]$Matches[2]; $pat = [int]$Matches[3]
switch ($Bump) {
    'major' { $maj++; $min = 0; $pat = 0 }
    'minor' { $min++; $pat = 0 }
    'patch' { $pat++ }
}
$new = "$maj.$min.$pat"
$mode = if ($Apply) { 'APPLY' } else { 'DRY RUN (add -Apply to write)' }
"$mode  groundwork: $current -> $new"

foreach ($m in $manifests) {
    $head = $m.text.Substring(0, $m.start)
    $tail = $m.text.Substring($m.start)
    $count = if ($m.anchor) { -1 } else { 1 }
    $tail = $rx.Replace($tail, "`${1}$new`${3}", $count)
    if ($Apply) { [IO.File]::WriteAllText($m.path, $head + $tail, $utf8) }
    "  $($m.rel): $new ($($m.expect) field(s))"
}

$touched = ($manifests | ForEach-Object { $_.rel }) -join ' '
''
'Next, in PowerShell, cwd the repo root:'
"  cd `"$Root`""
"  git add -- $touched"
"  git commit -m `"release: v$new`""
"  git tag -a v$new -m `"v$new`""
'  git push'
"  git push origin v$new"
exit 0
