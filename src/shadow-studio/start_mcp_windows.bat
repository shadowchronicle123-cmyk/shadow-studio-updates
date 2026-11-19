@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install app dependencies with your existing Python environment first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" shadow_mcp.py
pause
