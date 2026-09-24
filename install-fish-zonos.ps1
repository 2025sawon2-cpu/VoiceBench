param([ValidateSet('fish-speech', 'zonos2', 'all')][string]$Engine = 'all')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$harness = Join-Path $root '.venv\Scripts\python.exe'
function Check([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed ($LASTEXITCODE)" } }
if (-not (Test-Path -LiteralPath $harness)) { throw 'Run bootstrap.ps1 first.' }
$targets = if ($Engine -eq 'all') { @('fish-speech','zonos2') } else { @($Engine) }
foreach ($target in $targets) {
    $runtime = Join-Path $root ".runtime\engines\$target"
    $python = Join-Path $runtime 'venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        & $harness -m venv (Join-Path $runtime 'venv'); Check 'Create environment'
    }
    & $python -m pip install uv==0.12.18; Check 'Install uv'
    $uv = Join-Path $runtime 'venv\Scripts\uv.exe'
    if ($target -eq 'fish-speech') {
        Write-Host 'Built with Fish Audio. S2-Pro is for research/non-commercial evaluation; commercial use requires a separate Fish Audio license.'
        $source = Join-Path $runtime 'source'
        $revision = '214da3cd841bda85da2496b96cd3c4d7edb1337e'
        if (-not (Test-Path -LiteralPath $source)) {
            git clone https://github.com/fishaudio/fish-speech.git $source; Check 'Clone Fish'
            git -C $source checkout $revision; Check 'Pin Fish'
        } elseif ((git -C $source rev-parse HEAD).Trim() -ne $revision) {
            throw 'Existing Fish source has a different revision; preserve it and review before updating.'
        }
        $overrides = Join-Path $runtime 'overrides.txt'
        'protobuf>=3.20.0,<6.0.0' | Set-Content -LiteralPath $overrides
        & $uv pip install --python $python -e $source 'tensorboard==2.18.0' 'googleapis-common-protos==1.70.0' --extra-index-url https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match --override $overrides
        Check 'Install Fish CUDA dependencies'
    } else {
        & $uv pip install --python $python 'numpy==2.2.6' 'soundfile==0.14.0' 'huggingface-hub==0.36.2' 'torch==2.8.0+cu126' 'torchaudio==2.8.0+cu126' 'transformers==4.57.3' --extra-index-url https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match
        Check 'Install ZONOS2 bridge and official speaker encoder dependencies'
    }
    & $python (Join-Path $root 'tools\download_fish_zonos.py') $target; Check 'Download models'
    if ($target -eq 'zonos2') {
        & (Join-Path $root 'build-zonos2-cuda.ps1')
    }
    Push-Location $root
    try { & $harness tools\register_fish_zonos.py $target; Check 'Register engine' }
    finally { Pop-Location }
}
