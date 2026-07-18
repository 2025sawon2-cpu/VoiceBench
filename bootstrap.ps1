param(
    [string]$PythonVersion = '3.10'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-Host "VoiceBench virtual environment already exists: $venvPython"
    exit 0
}

$launcher = Get-Command py -ErrorAction SilentlyContinue
if (-not $launcher) {
    throw "Python Launcher (py.exe) was not found. Install 64-bit Python $PythonVersion or newer from python.org, then run this script again."
}

Write-Host "Creating an isolated VoiceBench environment with Python $PythonVersion..."
& $launcher.Source "-$PythonVersion" -m venv (Join-Path $root '.venv')

if (-not (Test-Path $venvPython)) {
    throw 'Virtual environment creation failed.'
}

& $venvPython -c "import sys; print('VoiceBench Python:', sys.executable); print(sys.version)"
Write-Host 'Bootstrap complete. Run .\start.ps1 to launch VoiceBench.'
