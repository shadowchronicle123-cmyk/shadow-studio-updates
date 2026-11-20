# Opt-in, least-privilege Shadow Studio Windows Task Scheduler installer.
# Does nothing unless the owner passes -Install. Never schedules YouTube uploads.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [ValidateRange(5,60)][int]$IntervalMinutes = 10,
    [string]$TaskName = 'ShadowStudioLocalProduction',
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
if ($TaskName -notmatch '^[A-Za-z][A-Za-z0-9_-]{3,60}$') {
    throw 'Use a simple local Task Scheduler name.'
}
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$cycle = Join-Path $repo 'src\shadow-studio\local_cycle.py'
if (-not (Test-Path -LiteralPath $cycle -PathType Leaf)) {
    throw 'Local cycle runner is missing. Check out the correct development branch.'
}
$pythonCmd = Get-Command 'python.exe' -ErrorAction Stop
$python = $pythonCmd.Source
& $python -c 'import sys;sys.exit(0 if sys.version_info >= (3,12) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
# Validate config, approved media and timezone before registration.
& $python $cycle --config $config check
if ($LASTEXITCODE -ne 0) {
    throw 'Production configuration or authorized footage validation failed.'
}
$workdir = Split-Path -Path $cycle -Parent
$args = '-u "{0}" --config "{1}" once' -f $cycle, $config
$action = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory $workdir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited

if (-not $Install) {
    Write-Output 'Dry-run only: no Windows task was installed.'
    Write-Output ('Task: {0}; run as {1}; every {2} minutes; private local production only.' -f $TaskName,$identity,$IntervalMinutes)
    Write-Output 'Use -Install only after reviewing the approved-media manifest and CI results.'
    return
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'An existing task has this name. Refusing to replace it.'
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal | Out-Null
Write-Output ('Installed local-only task: {0}. No YouTube publishing was enabled.' -f $TaskName)
