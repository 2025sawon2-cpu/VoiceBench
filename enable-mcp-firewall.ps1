# Run in an administrator PowerShell; exposes only this gateway to LocalSubnet.
$ErrorActionPreference = 'Stop'
$ruleName = 'VoiceBench-LAN-MCP-8766'
if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
    Write-Host 'Existing VoiceBench MCP firewall rule preserved.'
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName 'VoiceBench LAN MCP / API' -Direction Inbound `
        -Action Allow -Protocol TCP -LocalPort 8766 -Profile Any -RemoteAddress LocalSubnet | Out-Null
    Write-Host 'Enabled TCP 8766 for LocalSubnet (also works when the LAN profile is Public).'
}
