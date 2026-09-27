@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\start_studio.py"
if errorlevel 1 pause
