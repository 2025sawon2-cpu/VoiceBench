param(
    [switch]$DownloadModels,
    [string]$ReferenceWav,
    [string]$ReferenceText
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$harnessPython = Join-Path $root '.venv\Scripts\python.exe'
$referenceDirectory = Join-Path $root 'data\prepared\ref_voice'
$selectedWav = Join-Path $referenceDirectory 'selected_reference.wav'
$selectedText = Join-Path $referenceDirectory 'selected_reference.txt'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This setup script supports Windows only.'
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher (py.exe) was not found. Install 64-bit Python 3.10 from python.org.'
}

if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    throw 'nvidia-smi was not found. Install a current NVIDIA driver before setting up Qwen3-TTS.'
}

if ([bool]$ReferenceWav -ne [bool]$ReferenceText) {
    throw 'Provide both -ReferenceWav and -ReferenceText, or omit both.'
}

Set-Location $root
Write-Host 'Preparing the VoiceBench application environment...'
& (Join-Path $root 'bootstrap.ps1')

Write-Host 'Preparing the Qwen3-TTS engine environment...'
& (Join-Path $root 'install-qwen3-tts.ps1') -DownloadModel:$DownloadModels

if ($ReferenceWav -and $ReferenceText) {
    $sourceWav = (Resolve-Path -LiteralPath $ReferenceWav).Path
    $sourceText = (Resolve-Path -LiteralPath $ReferenceText).Path
    if ([IO.Path]::GetExtension($sourceWav).ToLowerInvariant() -ne '.wav') {
        throw 'ReferenceWav must point to a WAV file.'
    }
    New-Item -ItemType Directory -Force -Path $referenceDirectory | Out-Null
    Copy-Item -LiteralPath $sourceWav -Destination $selectedWav -Force
    Copy-Item -LiteralPath $sourceText -Destination $selectedText -Force
    if (-not [IO.File]::ReadAllText($selectedText, [Text.Encoding]::UTF8).Trim()) {
        throw 'The reference transcript is empty.'
    }
    Write-Host "Installed the local reference voice: $selectedWav"
}
elseif (-not ((Test-Path $selectedWav) -and (Test-Path $selectedText))) {
    Write-Warning 'The app is installed, but voice cloning remains unavailable until selected_reference.wav and selected_reference.txt are copied into data\prepared\ref_voice.'
}

Write-Host 'Running the VoiceBench regression suite...'
& $harnessPython -m unittest discover -s tests -v
Assert-NativeSuccess 'VoiceBench regression suite'

Write-Host ''
Write-Host 'VoiceBench setup is complete.' -ForegroundColor Green
Write-Host 'Local start: .\start.cmd'
Write-Host 'LAN start (Administrator PowerShell): .\host-lan.cmd'
