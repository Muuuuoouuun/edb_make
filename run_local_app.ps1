param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$InstallDeps,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not $PythonExe) {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

if ($InstallDeps) {
    & $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements-local.txt")
}

$Arguments = @("app_server.py", "--host", "127.0.0.1", "--port", $Port)
if (-not $NoBrowser) {
    $Arguments += "--open-browser"
}

Write-Host "Launching local MVP app on http://127.0.0.1:$Port"
& $PythonExe @Arguments
