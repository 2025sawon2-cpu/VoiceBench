param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    & (Join-Path $root 'bootstrap.ps1')
}

if (-not (Test-Path $python)) {
    throw 'VoiceBench Python environment is unavailable. Run .\bootstrap.ps1 and review the error.'
}

Set-Location $root
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($Url)
        Start-Sleep -Seconds 2
        Start-Process $Url
    } -ArgumentList "http://127.0.0.1:$Port" | Out-Null
}

& $python -m voicebench --host 127.0.0.1 --port $Port
