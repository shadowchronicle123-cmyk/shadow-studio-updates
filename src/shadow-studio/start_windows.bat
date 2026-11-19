@echo off
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul
if not exist ".venv\Scripts\python.exe" (py -3 -m venv .venv || goto failed)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt || goto failed
start "" "http://127.0.0.1:8765"
python -m uvicorn app:app --host 127.0.0.1 --port 8765
echo.
echo Shadow Studio has stopped. Press any key to close.
pause >nul
exit /b 0
:failed
echo Setup failed. Install Python 3.11+ and FFmpeg, then try again.
pause
