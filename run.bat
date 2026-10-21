@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] creating virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
)
echo [2/3] installing requirements...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo [3/3] starting...
".venv\Scripts\python.exe" run.py
pause
