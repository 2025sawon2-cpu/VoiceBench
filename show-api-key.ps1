$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$apiKeyFile = Join-Path $root '.runtime\external-api-key.txt'

if (-not (Test-Path $apiKeyFile)) {
    throw 'The external API key has not been generated. Start hosting once first.'
}

Write-Host 'VoiceBench External TTS API key:' -ForegroundColor Cyan
Write-Output ([IO.File]::ReadAllText($apiKeyFile).Trim())
Write-Host 'Keep this key private. Anyone with it can generate audio with your voice.' -ForegroundColor Yellow
