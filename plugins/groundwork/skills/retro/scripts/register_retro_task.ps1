# Plans or registers the monthly retro scan as a Windows scheduled task.
# Runs in: PowerShell 7 or Windows PowerShell 5.1, any folder.
# By default this only WRITES the hidden-window shim <Root>\retro\run_retro_hidden.vbs and
# PRINTS the plan. Nothing is registered until you add -Register. Registering is your step.
#
# Why a shim: a task under Interactive logon shows a console window even with
# -WindowStyle Hidden. wscript.exe has no console, and Run(cmd, 0, True) starts the
# hidden PowerShell wrapper AND waits, so its exit code becomes the task's Last Result.
# Use False there and the task would report success whether or not the scan ran.
# /IT is Interactive logon: some managed laptops refuse batch logon, so no service
# account and no stored password is used.
#
# Prerequisite: run retro_config.py init for the same -Root once. The wrapper never prompts.
#   -Verify  reads the task back and exits with its last result (0 proves the last run).
param(
    [string]$Root,
    [string]$Python = 'py',
    [string]$TaskName = 'GroundworkRetroMonthly',
    [int]$Day = 1,
    [string]$Time = '09:00',
    [switch]$Register,
    [switch]$Verify,
    [string]$VerifyUseResult
)
$ErrorActionPreference = 'Stop'
$scriptsDir = $PSScriptRoot
$wrapper = Join-Path $scriptsDir 'run_retro_task.ps1'
function Get-CleanRoot([string]$Path) {
    if ($Path.Length -gt 3) { $Path = $Path.TrimEnd(([char]92), '/') }
    return $Path
}
# Inside the VBS string a quote is written twice. A trailing backslash (only a bare drive
# root can still have one) is doubled so it cannot escape the closing quote.
function Format-VbsArg([string]$Value) {
    if ($Value.EndsWith([string][char]92)) { $Value = $Value + [char]92 }
    return '""' + $Value + '""'
}

if ($Verify) {
    # Locale independent: the Task Scheduler cmdlet returns a number, no label text is parsed.
    # -VerifyUseResult lets a test feed a number without asking the scheduler.
    try {
        if ($VerifyUseResult) { $v = [uint32]$VerifyUseResult }
        else { $v = [uint32](Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult }
    } catch {
        Write-Output ('Could not read task ' + $TaskName + ' (' + $_.Exception.GetType().Name + '). Is it registered?')
        exit 1
    }
    if ($v -eq 0) { Write-Output 'Last run result 0: success.' }
    elseif ($v -eq 267011) { Write-Output 'Last run result 267011: the task has never run yet.' }
    else { Write-Output ('Last run result ' + $v + ': the last run failed, see scheduled_runs.log in the retro folder.') }
    exit ([BitConverter]::ToInt32([BitConverter]::GetBytes($v), 0))
}

if (-not $Root) { Write-Output 'Root is required: pass -Root <your root>.'; exit 2 }
if ($Day -lt 1 -or $Day -gt 28) { Write-Output 'Day must be between 1 and 28.'; exit 2 }
if ($Time -notmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') { Write-Output 'Time must be HH:MM, 24 hour, for example 09:00.'; exit 2 }
if ($TaskName -match ('["' + [regex]::Escape([string][char]92) + '/]')) { Write-Output 'TaskName must be plain text without quotes or slashes.'; exit 2 }

$Root = Get-CleanRoot $Root
$retroDir = Join-Path $Root 'retro'
$vbs = Join-Path $retroDir 'run_retro_hidden.vbs'

$null = New-Item -ItemType Directory -Force -Path $retroDir
$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File {0} -Root {1} -Python {2}' -f (Format-VbsArg $wrapper), (Format-VbsArg $Root), (Format-VbsArg $Python)
$lines = @(
    'Set sh = CreateObject("WScript.Shell")',
    ('rc = sh.Run("{0}", 0, True)' -f $cmd),
    'WScript.Quit rc'
)
Set-Content -Path $vbs -Value $lines -Encoding ASCII

$tr = 'wscript.exe //B //Nologo \"{0}\"' -f $vbs
$create = '/Create /SC MONTHLY /D {0} /ST {1} /TN "{2}" /TR "{3}" /IT /F' -f $Day, $Time, $TaskName, $tr

Write-Output 'Plan for the monthly retro scan:'
Write-Output ('  Task name : ' + $TaskName)
Write-Output ('  Trigger   : MONTHLY, day {0} at {1}, Interactive logon (/IT)' -f $Day, $Time)
Write-Output ('  Action    : wscript.exe shim ' + $vbs)
Write-Output ('  Runs      : hidden PowerShell wrapper ' + $wrapper)
Write-Output '  Scripts   : the wrapper calls only retro_report.py (collect, estimate, report in one call)'
Write-Output ('  Log       : ' + (Join-Path $retroDir 'scheduled_runs.log') + ' (one line per run)')
Write-Output '  Exit code : the wrapper returns the python exit code; prove it with -Verify (Last Result 0)'
if (-not (Test-Path -LiteralPath (Join-Path $retroDir 'config.json'))) {
    Write-Output ('  Warning   : ' + (Join-Path $retroDir 'config.json') + ' is missing, run retro_config.py init first')
}
Write-Output 'Notes:'
Write-Output '  1. By default schtasks skips a run when the laptop is off or on battery at the trigger time.'
Write-Output '  2. If the groundwork plugin install folder moves, run this script again: the shim stores the wrapper path.'
Write-Output '  3. On macOS check that python3 is not the Xcode stub before using the launchd or cron example.'

if (-not $Register) {
    Write-Output 'Not registered. Run again with -Register to create the task:'
    Write-Output ('  schtasks.exe ' + $create)
    exit 0
}

$p = Start-Process -FilePath 'schtasks.exe' -ArgumentList $create -Wait -PassThru -NoNewWindow
Write-Output ('schtasks.exe exit code ' + $p.ExitCode)
Write-Output 'Next: run again with -Verify after the first scheduled run to read Last Result.'
exit $p.ExitCode
