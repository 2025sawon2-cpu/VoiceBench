$ErrorActionPreference = 'Stop'
$sessionPath = Join-Path $PSScriptRoot '.runtime\mcp-session.json'
if (-not (Test-Path -LiteralPath $sessionPath)) { Write-Host 'No managed MCP gateway.'; exit }
$session = Get-Content -LiteralPath $sessionPath -Raw | ConvertFrom-Json
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($session.pid)"
if ($taskProcess) {
    if (-not $taskProcess.CommandLine.Contains($session.command_path)) { throw 'PID belongs to another process; preserved.' }
    $taskChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($session.pid)"
    foreach ($taskChild in $taskChildren) {
        if ($taskChild.CommandLine.Contains($session.command_path)) { Stop-Process -Id $taskChild.ProcessId }
    }
    Stop-Process -Id $session.pid -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $sessionPath
Write-Host 'MCP gateway stopped. Studio preserved.'
