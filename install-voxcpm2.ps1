$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$harnessPython = Join-Path $root '.venv\Scripts\python.exe'
$engineRoot = Join-Path $root '.runtime\engines\voxcpm2'
$enginePython = Join-Path $engineRoot 'venv\Scripts\python.exe'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

if (-not (Test-Path $harnessPython)) { & (Join-Path $root 'bootstrap.ps1') }
if (-not (Test-Path $enginePython)) {
    & $harnessPython -m venv (Join-Path $engineRoot 'venv')
    Assert-NativeSuccess 'VoxCPM2 virtual environment creation'
}
& $enginePython -m pip install --upgrade 'pip<27'
Assert-NativeSuccess 'pip upgrade'
& $enginePython -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu126
Assert-NativeSuccess 'CUDA PyTorch installation'
& $enginePython -m pip install voxcpm==2.0.3 torch==2.6.0+cu126 torchaudio==2.6.0+cu126 transformers==4.57.6 numpy==1.26.4 gradio==6.17.3
Assert-NativeSuccess 'VoxCPM2 installation'
& $enginePython -c "import torch; from voxcpm import VoxCPM; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.__version__, torch.cuda.get_device_name(0))"
Assert-NativeSuccess 'VoxCPM2 CUDA verification'
& $enginePython (Join-Path $root 'tools\download_voxcpm2.py')
Assert-NativeSuccess 'VoxCPM2 model download'
Push-Location $root
try {
    & $harnessPython tools\register_voxcpm2.py
    Assert-NativeSuccess 'VoxCPM2 registration'
} finally { Pop-Location }
Write-Host 'VoxCPM2 is ready in the VoiceBench engine selector.'
