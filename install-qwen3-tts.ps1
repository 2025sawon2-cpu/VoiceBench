param(
    [switch]$DownloadModel
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$engineRoot = Join-Path $root '.runtime\engines\qwen3-tts'
$enginePython = Join-Path $engineRoot 'venv\Scripts\python.exe'
$harnessPython = Join-Path $root '.venv\Scripts\python.exe'
$modelCache = Join-Path $root '.runtime\cache\huggingface'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if ((Get-PSDrive -Name C).Free -lt 12GB) {
    throw 'At least 12 GB of free space is required to install and cache Qwen3-TTS safely.'
}

if (-not (Test-Path $harnessPython)) {
    & (Join-Path $root 'bootstrap.ps1')
}

if (-not (Test-Path $enginePython)) {
    New-Item -ItemType Directory -Force -Path $engineRoot | Out-Null
    Write-Host 'Creating isolated Qwen3-TTS environment...'
    & py -3.10 -m venv (Join-Path $engineRoot 'venv')
    Assert-NativeSuccess 'Qwen3-TTS virtual environment creation'
}

Write-Host 'Updating the engine package installer...'
& $enginePython -m pip install --upgrade "pip<27"
Assert-NativeSuccess 'pip upgrade'

Write-Host 'Installing Pascal-compatible PyTorch 2.6.0 with CUDA 12.6...'
& $enginePython -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126
Assert-NativeSuccess 'PyTorch installation'

Write-Host 'Installing the official Qwen3-TTS Python package...'
& $enginePython -m pip install qwen-tts==0.1.1
Assert-NativeSuccess 'Qwen3-TTS installation'

$env:HF_HOME = $modelCache
$env:HF_HUB_DISABLE_TELEMETRY = '1'
& $enginePython -c "import torch, qwen_tts; print('torch', torch.__version__, 'CUDA', torch.version.cuda, 'available', torch.cuda.is_available()); print('GPU', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
Assert-NativeSuccess 'Qwen3-TTS environment verification'

Set-Location $root
& $harnessPython tools\register_qwen3_tts.py
Assert-NativeSuccess 'VoiceBench engine registration'

if ($DownloadModel) {
    Write-Host 'Downloading Qwen3-TTS 0.6B model weights...'
    & $enginePython adapters\qwen3_tts.py --download-only
    Assert-NativeSuccess 'Qwen3-TTS model download'
}

Write-Host 'Qwen3-TTS installation and VoiceBench registration are complete.'
