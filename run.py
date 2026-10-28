"""تشغيل السيستم من المجلد الرئيسي: python run.py (أو run.bat). بيفتح المتصفح على http://127.0.0.1:5000"""
import runpy
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if __name__ == "__main__":
    runpy.run_path(str(BACKEND_DIR / "run.py"), run_name="__main__")
