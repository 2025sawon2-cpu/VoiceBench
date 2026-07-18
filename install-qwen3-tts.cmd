@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-qwen3-tts.ps1" %*
exit /b %errorlevel%
