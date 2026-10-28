@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
  echo [2/2] Installing requirements...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
rem Check and start OSRM server if available and not yet running
tasklist /FI "IMAGENAME eq osrm-routed.exe" 2>nul | find /I "osrm-routed.exe" >nul
if %errorlevel% neq 0 (
  if exist "%USERPROFILE%\osrm\bin\binding_napi_v8\osrm-routed.exe" (
    echo Starting OSRM on 127.0.0.1:5001...
    call tools\osrm\start_osrm.bat
  )
)

echo Starting Markazi on http://127.0.0.1:5000 ...
start "" "http://127.0.0.1:5000"
".venv\Scripts\python.exe" run.py
pause
