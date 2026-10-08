@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\start_mcp.py %*
if errorlevel 1 pause
