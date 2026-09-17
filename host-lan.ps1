param(
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ruleName = "VoiceBench LAN TTS ($Port)"

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    throw 'Run this script from an Administrator PowerShell window. It adds a Private-network firewall rule.'
}

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Private `
    -RemoteAddress LocalSubnet `
    -Description 'VoiceBench TTS API for trusted devices on the local private network only.' | Out-Null

& (Join-Path $root 'host.ps1') -Port $Port -BindAddress '0.0.0.0' -NoTunnel -Background
