param([string]$Architecture = '89', [ValidatePattern('^[D-Z]$')][string]$BuildDrive = 'V')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.runtime\engines\zonos2\venv\Scripts\python.exe'
function Check([string]$step) { if ($LASTEXITCODE -ne 0) { throw "$step failed ($LASTEXITCODE)" } }
if ($Architecture -notmatch '^\d{2,3}$') { throw 'Architecture must be a CUDA SM number.' }
& $python (Join-Path $root 'tools\prepare_zonos2_toolchain.py'); Check 'Prepare portable toolchain'
& $python -m pip install cmake==4.4.3 ninja==1.13.2; Check 'Install CMake/Ninja'
$source = Join-Path $root '.runtime\engines\zonos2\source'
$revision = '39a4d01558db86dca1219273992c77ebc8e03991'
if (-not (Test-Path -LiteralPath $source)) {
    git clone https://github.com/Zyphra/zonos2.cpp.git $source; Check 'Clone ZONOS2'
    git -C $source checkout $revision; Check 'Pin ZONOS2'
} elseif ((git -C $source rev-parse HEAD).Trim() -ne $revision) {
    throw 'Preserve existing source: different ZONOS2 revision found.'
}
git -C $source submodule update --init --depth 1; Check 'Get pinned GGML'
$drive = "${BuildDrive}:"
if (Test-Path "${drive}\") { throw "$drive is in use; choose -BuildDrive with an unused letter." }
# NVCC/Windows tools need ASCII paths. This maps only the existing workspace.
subst $drive $root; Check 'Map temporary build path'
try {
    $script = Join-Path $root '.runtime\toolchains\build-zonos-generated.cmd'
    @"
@echo off
call ${drive}\.runtime\toolchains\msvc\setup_x64.bat
set CUDA_PATH=${drive}\.runtime\toolchains\cuda-12.9
set PATH=${drive}\.runtime\engines\zonos2\venv\Scripts;%CUDA_PATH%\bin;%PATH%
cmake -S ${drive}\.runtime\engines\zonos2\source -B ${drive}\.runtime\engines\zonos2\build-cuda -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DGGML_VULKAN=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_AVX512=OFF -DCMAKE_CUDA_ARCHITECTURES=$Architecture -DBUILD_SHARED_LIBS=OFF -DZONOS2_APP=OFF -DCUDAToolkit_ROOT=%CUDA_PATH% -DCMAKE_CUDA_COMPILER=%CUDA_PATH%\bin\nvcc.exe
if errorlevel 1 exit /b %errorlevel%
cmake --build ${drive}\.runtime\engines\zonos2\build-cuda --target zonos2-server --parallel 4
"@ | Set-Content -Encoding ascii -LiteralPath $script
    & cmd.exe /d /c "${drive}\.runtime\toolchains\build-zonos-generated.cmd"; Check 'Build CUDA server'
} finally { subst $drive /D }
