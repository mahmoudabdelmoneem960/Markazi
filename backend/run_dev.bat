@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   تشغيل Markazi في وضع المطور والتحديث التلقائي (Live Reload)
echo ========================================================
if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" run.py --dev
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py --dev
) else (
  python run.py --dev
)
pause
