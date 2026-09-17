param(
    [int]$Port = 8765,
    [switch]$Background,
    [string]$BindAddress = '127.0.0.1',
    [switch]$NoTunnel
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$ngrok = Join-Path $root '.runtime\tools\ngrok.exe'
$stdoutLog = Join-Path $root '.runtime\hosting-voicebench.log'
$stderrLog = Join-Path $root '.runtime\hosting-voicebench-error.log'
$ngrokLog = Join-Path $root '.runtime\hosting-ngrok.log'
$ngrokErrorLog = Join-Path $root '.runtime\hosting-ngrok-error.log'
$sessionFile = Join-Path $root '.runtime\hosting-session.json'
$externalApiKeyFile = Join-Path $root '.runtime\external-api-key.txt'

if (-not (Test-Path $python)) {
    & (Join-Path $root 'bootstrap.ps1')
}
if (-not (Test-Path $python)) {
    throw 'VoiceBench Python environment is unavailable. Run .\bootstrap.ps1 first.'
}
if (-not (Test-Path $ngrok)) {
    if (-not $NoTunnel) {
        throw 'ngrok is unavailable. Re-run the hosting setup from Codex.'
    }
}

if ($BindAddress -notin @('127.0.0.1', '0.0.0.0')) {
    throw 'BindAddress must be 127.0.0.1 (local only) or 0.0.0.0 (LAN).'
}

$plainPassword = $env:VOICEBENCH_ACCESS_KEY
if (-not $plainPassword) {
    $securePassword = Read-Host 'Enter a web access password (8+ characters)' -AsSecureString
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
}
if ($plainPassword.Length -lt 8) {
    throw 'The web access password must contain at least 8 characters.'
}

$externalApiKey = $env:VOICEBENCH_EXTERNAL_API_KEY
if (-not $externalApiKey -and (Test-Path $externalApiKeyFile)) {
    $externalApiKey = [IO.File]::ReadAllText($externalApiKeyFile).Trim()
}
if (-not $externalApiKey) {
    $apiKeyBytes = New-Object byte[] 32
    $apiKeyGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $apiKeyGenerator.GetBytes($apiKeyBytes)
    }
    finally {
        $apiKeyGenerator.Dispose()
    }
    $externalApiKey = 'vb_' + [Convert]::ToBase64String($apiKeyBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    [IO.File]::WriteAllText($externalApiKeyFile, $externalApiKey, [Text.Encoding]::ASCII)
}
if ($externalApiKey.Length -lt 32) {
    throw 'The external API key is invalid.'
}

Set-Location $root
$previousAccessKey = $env:VOICEBENCH_ACCESS_KEY
$previousExternalApiKey = $env:VOICEBENCH_EXTERNAL_API_KEY
$env:VOICEBENCH_ACCESS_KEY = $plainPassword
$env:VOICEBENCH_EXTERNAL_API_KEY = $externalApiKey
$serverProcess = $null
$ngrokProcess = $null
$keepRunning = $false
try {
    $serverProcess = Start-Process -FilePath $python `
        -ArgumentList @('-m', 'voicebench', '--host', $BindAddress, '--port', "$Port") `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    $authorization = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("voice:$plainPassword"))
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "VoiceBench failed to start. Review $stderrLog"
        }
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/state" `
                -Headers @{ Authorization = "Basic $authorization" } -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "VoiceBench did not become ready. Review $stderrLog"
    }
    $env:VOICEBENCH_EXTERNAL_API_KEY = $previousExternalApiKey

    if ($Background -and $NoTunnel) {
        $session = [ordered]@{
            url = "http://$BindAddress`:$Port"
            port = $Port
            bind_address = $BindAddress
            server_pid = $serverProcess.Id
            ngrok_pid = $null
            started_at = [DateTime]::UtcNow.ToString('o')
        }
        [IO.File]::WriteAllText($sessionFile, ($session | ConvertTo-Json), [Text.Encoding]::UTF8)
        $keepRunning = $true
        Write-Output "http://$BindAddress`:$Port"
    }
    elseif ($Background) {
        $env:VOICEBENCH_ACCESS_KEY = $previousAccessKey
        $ngrokProcess = Start-Process -FilePath $ngrok `
            -ArgumentList @('http', "http://127.0.0.1:$Port", '--log=stdout', '--log-format=json') `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $ngrokLog `
            -RedirectStandardError $ngrokErrorLog `
            -PassThru

        $publicUrl = $null
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            if ($ngrokProcess.HasExited) {
                throw "ngrok failed to start. Review $ngrokErrorLog"
            }
            try {
                $tunnels = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
                $publicUrl = $tunnels.tunnels | Where-Object { $_.public_url -like 'https://*' } |
                    Select-Object -ExpandProperty public_url -First 1
                if ($publicUrl) { break }
            }
            catch {
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $publicUrl) {
            throw "ngrok did not publish a URL. Review $ngrokErrorLog"
        }

        $session = [ordered]@{
            url = $publicUrl
            port = $Port
            bind_address = $BindAddress
            server_pid = $serverProcess.Id
            ngrok_pid = $ngrokProcess.Id
            started_at = [DateTime]::UtcNow.ToString('o')
        }
        [IO.File]::WriteAllText($sessionFile, ($session | ConvertTo-Json), [Text.Encoding]::UTF8)
        $keepRunning = $true
        Write-Output $publicUrl
    }
    elseif (-not $NoTunnel) {
        Write-Host ''
        Write-Host 'Starting VoiceBench web hosting.' -ForegroundColor Green
        Write-Host 'Login username: voice' -ForegroundColor Cyan
        Write-Host 'Password: the value you just entered' -ForegroundColor Cyan
        Write-Host "External API key file: $externalApiKeyFile" -ForegroundColor Cyan
        Write-Host 'Open the Forwarding https://...ngrok-free.app URL shown below.' -ForegroundColor Yellow
        Write-Host 'Close this window or press Ctrl+C to stop both hosting and TTS.' -ForegroundColor Yellow
        Write-Host ''

        $plainPassword = $null
        $authorization = $null
        & $ngrok http "http://127.0.0.1:$Port"
    }
    else {
        throw 'NoTunnel requires -Background so the server can remain running.'
    }
}
finally {
    $env:VOICEBENCH_ACCESS_KEY = $previousAccessKey
    $env:VOICEBENCH_EXTERNAL_API_KEY = $previousExternalApiKey
    $plainPassword = $null
    $externalApiKey = $null
    if (-not $keepRunning -and $ngrokProcess -and -not $ngrokProcess.HasExited) {
        Stop-Process -Id $ngrokProcess.Id -Force -ErrorAction SilentlyContinue
        $ngrokProcess.WaitForExit(5000) | Out-Null
    }
    if (-not $keepRunning -and $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
        $serverProcess.WaitForExit(5000) | Out-Null
    }
}
