@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" pc_acceptance.py
) else (
  where py >nul 2>&1
  if errorlevel 1 (
    echo FAIL: Install Python 3.11 or newer, or prepare the test copy's .venv.
    if /i not "%~1"=="/nopause" pause
    exit /b 1
  )
  py -3 pc_acceptance.py
)
set "result=%errorlevel%"
if /i not "%~1"=="/nopause" pause
exit /b %result%
