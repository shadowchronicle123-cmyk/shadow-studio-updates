@echo off
setlocal
cd /d "%~dp0"
echo SHADOW STUDIO - VERIFIED UPDATE
 echo Close the running Shadow Studio Command Prompt before updating.
 echo This updater preserves projects, videos, gameplay and your .env file.
 echo.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" update_manager.py install
) else (
  py -3 update_manager.py install
)
if errorlevel 1 (
  echo Update was not installed. Existing files remain unchanged if verification failed.
  pause
  exit /b 1
)
echo Update completed. Start Shadow Studio normally.
pause
