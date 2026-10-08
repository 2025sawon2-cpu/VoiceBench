$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'VoiceBench .venv is missing.' }
$mcpPython = Join-Path $PSScriptRoot '.runtime\mcp\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $mcpPython)) {
    & $taskPython -m venv '.runtime\mcp\venv'
    if ($LASTEXITCODE -ne 0) { throw 'MCP environment creation failed.' }
}
& $mcpPython -m pip install -r requirements-mcp.txt
if ($LASTEXITCODE -ne 0) { throw 'MCP installation failed.' }
& $mcpPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'MCP dependency check failed.' }
Write-Host 'Ready. Run mcp-lan.cmd.'
