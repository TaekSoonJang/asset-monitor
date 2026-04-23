param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [Parameter(Mandatory = $true)]
    [string]$ProjectDir,

    [string]$TaskName = "ShinhanAssetMonitor",

    [string]$StartBoundary = "2026-04-22T09:00:00"
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ProjectDir\scripts\run_asset_monitor.ps1`" -PythonExe `"$PythonExe`"" `
    -WorkingDirectory $ProjectDir

$trigger = New-ScheduledTaskTrigger -Once -At ([datetime]::Parse($StartBoundary)) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Collect Shinhan asset snapshots and sync them to Google Sheets every hour."
