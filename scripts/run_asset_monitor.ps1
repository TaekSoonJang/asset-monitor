param(
    [string]$PythonExe = ""
)

$projectDir = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"

if (-not $PythonExe) {
    $PythonExe = $venvPython
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

Set-Location -LiteralPath $projectDir
& $PythonExe -m asset_monitor.cli
exit $LASTEXITCODE
