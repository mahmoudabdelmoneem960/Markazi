@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo   تشغيل Markazi في وضع المطور والتحديث التلقائي (Live Reload)
echo   أي تعديل في الواجهة (HTML/CSS/JS) أو كود بايثون هيظهر فوراً
echo ========================================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run.py --dev
) else (
  python run.py --dev
)
pause
