$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sessionFile = Join-Path $root '.runtime\hosting-session.json'

if (-not (Test-Path $sessionFile)) {
    Write-Host 'No saved VoiceBench hosting session was found.'
    exit 0
}

$session = Get-Content -Raw -LiteralPath $sessionFile | ConvertFrom-Json
foreach ($entry in @(
    @{ Id = [int]$session.ngrok_pid; ExpectedName = 'ngrok' },
    @{ Id = [int]$session.server_pid; ExpectedName = 'python' }
)) {
    $process = Get-Process -Id $entry.Id -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq $entry.ExpectedName) {
        Stop-Process -Id $entry.Id -Force
        $process.WaitForExit(5000) | Out-Null
    }
}

$resolvedSession = [IO.Path]::GetFullPath($sessionFile)
$resolvedRuntime = [IO.Path]::GetFullPath((Join-Path $root '.runtime'))
if ($resolvedSession.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolvedSession -Force
}
Write-Host 'VoiceBench hosting stopped.'
