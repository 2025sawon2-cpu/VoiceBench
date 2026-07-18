@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0show-api-key.ps1" %*
exit /b %errorlevel%
