param(
    [switch]$DownloadModels,
    [switch]$Plan,
    [switch]$NoBrowser,
    [int]$GpuIndex = -1,
    [string]$ReferenceWav,
    [string]$ReferenceText
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    throw 'An NVIDIA GPU and driver are required. Install the driver, then run setup-windows.cmd again.'
}
# DownloadModels remains accepted for older instructions. Full download is now the default.
if (-not (Test-Path -LiteralPath $python)) {
    $basePython = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { $candidate = & py -3.10 -c 'import sys; print(sys.executable)' 2>$null }
        catch { $candidate = $null }
        if ($LASTEXITCODE -eq 0 -and $candidate) { $basePython = $candidate.Trim() }
    }
    $userPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'
    if (-not $basePython -and (Test-Path -LiteralPath $userPython)) { $basePython = $userPython }
    if (-not $basePython) {
        if ($Plan) { throw 'Python 3.10 is required for -Plan. Run setup-windows.cmd to install it.' }
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Install 64-bit Python 3.10 from python.org, then run setup-windows.cmd again.'
        }
        Write-Host 'Installing Python 3.10 for the current Windows user...'
        & winget install --id Python.Python.3.10 --exact --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $userPython)) {
            throw 'Python installation failed. Install 64-bit Python 3.10 manually, then retry.'
        }
        $basePython = $userPython
    }
    if ($Plan) {
        $python = $basePython
    } else {
        & $basePython -m venv (Join-Path $root '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'VoiceBench virtual environment creation failed.' }
    }
}
$arguments = @((Join-Path $root 'tools\setup_voicebench.py'))
if ($Plan) { $arguments += '--plan' }
if ($NoBrowser) { $arguments += '--no-browser' }
if ($GpuIndex -ge 0) { $arguments += @('--gpu-index', [string]$GpuIndex) }
if ($ReferenceWav) { $arguments += @('--reference-wav', $ReferenceWav) }
if ($ReferenceText) { $arguments += @('--reference-text', $ReferenceText) }
$env:PYTHONUTF8 = '1'
& $python @arguments
exit $LASTEXITCODE
