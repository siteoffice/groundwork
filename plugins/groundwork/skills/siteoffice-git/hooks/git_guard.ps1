# git_guard.ps1 - PreToolUse guard for the siteoffice-git skill.
# Pure ASCII, Windows PowerShell 5.1 compatible. Single source: skills\siteoffice-git\hooks\.
#
# PROTOCOL (verified 2026-09-30 against https://code.claude.com/docs/en/hooks):
#   stdin  : one JSON object with tool_name ("Bash" or "PowerShell") and tool_input.command.
#   deny   : exit code 2 and the reason on stderr. Doc: "Exit 2 means a blocking error. On events
#            that can block, exit 2 blocks whether or not you print JSON". PreToolUse can block,
#            and the stderr text is shown to Claude.
#   allow  : exit 0 with NO output. Doc: "Exit 0 with no output means the hook has no decision to
#            report, so the tool call continues through the normal permission flow." This script
#            never prints a JSON permissionDecision of allow, because that would skip the prompt.
# This script uses the exit-code form only (exit 2 + stderr), never the JSON form.
#
# BEHAVIOUR: see the design spec 2026-09-30-w3-1-12b. Quoted strings, here-strings and heredoc
# bodies are data. Commands are split on && || ; | & ( ) { } and newlines. A segment that invokes
# git with a denied verb is denied. Text nested in bash/sh -c, pwsh/powershell -Command, cmd /c, iex
# and $( ) inside double quotes is scanned too. SITEOFFICE_GIT_GUARD=off (read from the environment Claude Code
# was launched with) allows everything. An internal error denies when the input mentions git,
# otherwise allows.

$ErrorActionPreference = 'Stop'

function Get-Launcher {
    $p = Join-Path $PSScriptRoot '..\scripts\git_flow.ps1'
    return [System.IO.Path]::GetFullPath($p)
}

function Protect-Quoted {
    # Replaces quoted strings, here-strings and heredoc bodies by __Q<n>__ placeholders.
    # $Table receives the inner text of each. $Extra receives $( ... ) payloads found inside
    # double-quoted strings, because those run as commands.
    param([string]$Text, $Table, $Extra)
    $sb = New-Object System.Text.StringBuilder
    $n = $Text.Length
    $i = 0
    $pending = $null
    $reHere = New-Object System.Text.RegularExpressions.Regex('\G@([''"])[ \t]*\r?\n(.*?)\r?\n\1@', 'Singleline')
    $reOpen = New-Object System.Text.RegularExpressions.Regex('\G<<-?[ \t]*([''"]?)([A-Za-z_][A-Za-z0-9_]*)\1')
    while ($i -lt $n) {
        $c = $Text[$i]
        if ($c -eq '@') {
            $m = $reHere.Match($Text, $i)
            if ($m.Success) {
                [void]$Table.Add($m.Groups[2].Value)
                [void]$sb.Append('__Q' + ($Table.Count - 1) + '__')
                $i += $m.Length
                continue
            }
        }
        if ($c -eq '<' -and ($i + 2) -lt $n -and $Text[$i + 1] -eq '<' -and $Text[$i + 2] -ne '<') {
            $m = $reOpen.Match($Text, $i)
            if ($m.Success) {
                $pending = $m.Groups[2].Value
                [void]$sb.Append($m.Value)
                $i += $m.Length
                continue
            }
        }
        if (($c -eq "`n") -and $pending) {
            $reEnd = New-Object System.Text.RegularExpressions.Regex(('\G(.*?)^[ \t]*' + [regex]::Escape($pending) + '[ \t]*\r?$'), 'Singleline,Multiline')
            [void]$sb.Append($c)
            $i++
            $m = $reEnd.Match($Text, $i)
            if ($m.Success) {
                [void]$Table.Add($m.Groups[1].Value)
                $i += $m.Length
            } else {
                [void]$Table.Add($Text.Substring($i))
                $i = $n
            }
            [void]$sb.Append('__Q' + ($Table.Count - 1) + '__')
            $pending = $null
            continue
        }
        if ($c -eq "'") {
            $j = $i + 1
            $close = -1
            while ($j -lt $n) {
                if ($Text[$j] -eq "'") {
                    if (($j + 1) -lt $n -and $Text[$j + 1] -eq "'") { $j += 2; continue }
                    $close = $j
                    break
                }
                $j++
            }
            if ($close -ge 0) {
                [void]$Table.Add($Text.Substring($i + 1, $close - $i - 1))
                [void]$sb.Append('__Q' + ($Table.Count - 1) + '__')
                $i = $close + 1
                continue
            }
        }
        if ($c -eq '"') {
            $close = -1
            foreach ($honourBackslash in @($true, $false)) {
                $j = $i + 1
                while ($j -lt $n) {
                    $d = $Text[$j]
                    if ($d -eq '`' -or ($honourBackslash -and $d -eq '\')) { $j += 2; continue }
                    if ($d -eq '"') {
                        if (($j + 1) -lt $n -and $Text[$j + 1] -eq '"') { $j += 2; continue }
                        $close = $j
                        break
                    }
                    $j++
                }
                if ($close -ge 0) { break }
            }
            if ($close -ge 0) {
                $inner = $Text.Substring($i + 1, $close - $i - 1)
                [void]$Table.Add($inner)
                foreach ($sm in [regex]::Matches($inner, '\$\(([^()]*)\)')) { [void]$Extra.Add($sm.Groups[1].Value) }
                [void]$sb.Append('__Q' + ($Table.Count - 1) + '__')
                $i = $close + 1
                continue
            }
        }
        [void]$sb.Append($c)
        $i++
    }
    return $sb.ToString()
}

function Expand-Token {
    param([string]$Token, $Table)
    return [regex]::Replace($Token, '__Q(\d+)__', { param($m) $Table[[int]$m.Groups[1].Value] })
}

function Get-Leaf {
    param([string]$Path)
    $leaf = ($Path -replace '\\', '/').TrimEnd('/')
    $leaf = $leaf.Substring($leaf.LastIndexOf('/') + 1).ToLowerInvariant()
    if ($leaf.EndsWith('.exe')) { $leaf = $leaf.Substring(0, $leaf.Length - 4) }
    return $leaf
}

function Get-DeniedVerb {
    # $Verb is the git subcommand, $GitArgs the tokens after it. Returns the denied form or $null.
    param([string]$Verb, $GitArgs)
    $v = $Verb.ToLowerInvariant()
    switch ($v) {
        { $_ -in @('push', 'merge', 'rebase', 'commit', 'restore', 'pull', 'cherry-pick', 'revert') } { return $v }
        'reset' { if (@($GitArgs | Where-Object { $_ -ieq '--hard' }).Count -gt 0) { return 'reset --hard' } }
        'branch' { if (@($GitArgs | Where-Object { $_ -ceq '-D' }).Count -gt 0) { return 'branch -D' } }
        'checkout' { if (@($GitArgs | Where-Object { $_ -eq '--' }).Count -gt 0) { return 'checkout --' } }
        'clean' { if (@($GitArgs | Where-Object { $_ -match '^-[A-Za-z]*f' -or $_ -ieq '--force' }).Count -gt 0) { return 'clean -f' } }
    }
    return $null
}

function Find-Denied {
    param([string]$Text, [string]$Launcher, [int]$Depth)
    if ($Depth -gt 4) { return 'nested command' }
    $table = New-Object System.Collections.ArrayList
    $extra = New-Object System.Collections.ArrayList
    $clean = Protect-Quoted -Text $Text -Table $table -Extra $extra
    foreach ($sub in $extra) {
        $r = Find-Denied -Text $sub -Launcher $Launcher -Depth ($Depth + 1)
        if ($r) { return $r }
    }
    $wrappers = @('exec', 'command', 'sudo', 'time', 'env', 'nohup', 'call', 'then', 'do', 'else', 'xargs', '!')
    foreach ($seg in [regex]::Split($clean, '&&|\|\||[;|&(){}\r\n]')) {
        $tokens = New-Object System.Collections.ArrayList
        foreach ($t in ($seg.Trim() -split '\s+')) { if ($t) { [void]$tokens.Add($t) } }
        while ($tokens.Count -gt 0 -and (($wrappers -contains $tokens[0].ToLowerInvariant()) -or ($tokens[0] -match '^[A-Za-z_][A-Za-z0-9_]*=\S*$'))) {
            $tokens.RemoveAt(0)
        }
        if ($tokens.Count -eq 0) { continue }
        $exp = @($tokens | ForEach-Object { Expand-Token -Token $_ -Table $table })
        $name = Get-Leaf $exp[0]
        $tail = @()
        if ($exp.Count -gt 1) { $tail = @($exp[1..($exp.Count - 1)]) }

        # (f) the skill's own launcher, plain, quoted, with & or with pwsh -File
        $probe = $exp
        if ($name -in @('pwsh', 'powershell')) {
            for ($k = 1; $k -lt $exp.Count; $k++) {
                if ($exp[$k] -match '^-f(ile)?$') { $probe = @($exp[($k + 1)..($exp.Count - 1)]); break }
            }
        }
        if ($probe.Count -gt 0) {
            $lead = ($probe[0] -replace '/', '\').ToLowerInvariant()
            if ($lead -eq $Launcher.ToLowerInvariant()) { continue }
        }

        # (d) recurse into shell payloads
        $payload = $null
        if ($name -in @('bash', 'sh', 'zsh', 'dash')) {
            for ($k = 1; $k -lt ($exp.Count - 1); $k++) {
                if ($exp[$k] -match '^-[A-Za-z]*c$') { $payload = $exp[$k + 1]; break }
            }
        } elseif ($name -in @('pwsh', 'powershell')) {
            for ($k = 1; $k -lt ($exp.Count - 1); $k++) {
                if ($exp[$k] -match '^-c(o(m(m(a(n(d)?)?)?)?)?)?$') { $payload = ($exp[($k + 1)..($exp.Count - 1)] -join ' '); break }
            }
        } elseif ($name -eq 'cmd') {
            for ($k = 1; $k -lt ($exp.Count - 1); $k++) {
                if ($exp[$k] -match '^/[ck]$') { $payload = ($exp[($k + 1)..($exp.Count - 1)] -join ' '); break }
            }
        } elseif ($name -in @('iex', 'invoke-expression', 'eval')) {
            if ($tail.Count -gt 0) { $payload = ($tail -join ' ') }
        }
        if ($null -ne $payload) {
            $r = Find-Denied -Text $payload -Launcher $Launcher -Depth ($Depth + 1)
            if ($r) { return $r }
            continue
        }

        # (e) git itself
        if ($name -ne 'git') { continue }
        $i = 0
        while ($i -lt $tail.Count -and $tail[$i].StartsWith('-')) {
            if ($tail[$i] -in @('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix', '--config-env')) { $i += 2 } else { $i += 1 }
        }
        if ($i -ge $tail.Count) { continue }
        $rest = @()
        if (($i + 1) -lt $tail.Count) { $rest = @($tail[($i + 1)..($tail.Count - 1)]) }
        $r = Get-DeniedVerb -Verb $tail[$i] -GitArgs $rest
        if ($r) { return $r }
    }
    return $null
}

function Write-Deny {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
    exit 2
}

$raw = ''
try {
    if ($env:SITEOFFICE_GIT_GUARD -eq 'off') { exit 0 }
    $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), (New-Object System.Text.UTF8Encoding($false)))
    $raw = $reader.ReadToEnd()
    $data = $raw | ConvertFrom-Json
    if ($data.tool_name -ne 'Bash' -and $data.tool_name -ne 'PowerShell') { exit 0 }
    $command = [string]$data.tool_input.command
    if ($command.IndexOf('git', [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { exit 0 }
    $launcher = Get-Launcher
    $verb = Find-Denied -Text $command -Launcher $launcher -Depth 0
    if ($verb) {
        Write-Deny ("Blocked: raw 'git $verb' is not allowed in an agent session; use the siteoffice-git launcher $launcher (steps start, save, check, propose, land, abort).")
    }
    exit 0
} catch {
    if ($raw -match '(?i)git') { Write-Deny ('guard error: ' + $_.Exception.Message) }
    exit 0
}
# END git_guard.ps1
