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
#   -Verify  reads the task back and exits with its Last Result (0 proves the last run).
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$Python = 'py',
    [string]$TaskName = 'GroundworkRetroMonthly',
    [int]$Day = 1,
    [string]$Time = '09:00',
    [switch]$Register,
    [switch]$Verify
)
$ErrorActionPreference = 'Stop'
$scriptsDir = $PSScriptRoot
$wrapper = Join-Path $scriptsDir 'run_retro_task.ps1'
$retroDir = Join-Path $Root 'retro'
$vbs = Join-Path $retroDir 'run_retro_hidden.vbs'

if ($Verify) {
    $text = (& schtasks.exe /Query /TN $TaskName /V /FO LIST) -join "`n"
    Write-Output $text
    $m = [regex]::Match($text, 'Last Result:\s*(-?\d+)')
    if (-not $m.Success) { Write-Output 'Last Result not found.'; exit 1 }
    Write-Output ('Last Result: ' + $m.Groups[1].Value)
    exit ([int]$m.Groups[1].Value)
}

$null = New-Item -ItemType Directory -Force -Path $retroDir
$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""{0}"" -Root ""{1}"" -Python ""{2}""' -f $wrapper, $Root, $Python
$lines = @(
    'Set sh = CreateObject("WScript.Shell")',
    ('rc = sh.Run("{0}", 0, True)' -f $cmd),
    'WScript.Quit rc'
)
Set-Content -Path $vbs -Value $lines -Encoding ASCII

$tr = 'wscript.exe //B //Nologo \"{0}\"' -f $vbs
$create = '/Create /SC MONTHLY /D {0} /ST {1} /TN {2} /TR "{3}" /IT /F' -f $Day, $Time, $TaskName, $tr

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

if (-not $Register) {
    Write-Output 'Not registered. Run again with -Register to create the task:'
    Write-Output ('  schtasks.exe ' + $create)
    exit 0
}

$p = Start-Process -FilePath 'schtasks.exe' -ArgumentList $create -Wait -PassThru -NoNewWindow
Write-Output ('schtasks.exe exit code ' + $p.ExitCode)
Write-Output 'Next: run again with -Verify after the first scheduled run to read Last Result.'
exit $p.ExitCode
