param(
    [string]$PythonExe = "C:\Users\Zbook15G5\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$projectDir = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

Set-Location -LiteralPath $projectDir
& $PythonExe -m asset_monitor.cli
