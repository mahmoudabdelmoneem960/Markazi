"""زرار «Markazi» في شريط الـ IDE: لو السيستم شغال بيفتح المتصفح عليه على طول، ولو مش شغال بيشغّله (وضع التطوير)
والسيرفر نفسه بيفتح المتصفح أول ما يقوم. التشغيل: python tools/open_markazi.py"""
import json
import os
import subprocess
import sys
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from distributor import config  # noqa: E402


def running_url():
    """رابط السيستم لو شغال. السيرفر بياخد بورت تاني (5002-5029) لو الأساسي مشغول، فبندوّر عليهم كلهم."""
    host = "127.0.0.1" if config.HOST in ("0.0.0.0", "") else config.HOST
    ports = [config.PORT] + [p for p in range(5002, 5030) if p != config.PORT]

    def check(port):
        url = f"http://{host}:{port}"
        try:
            with urllib.request.urlopen(url + "/health", timeout=1.5) as r:
                return url if json.loads(r.read().decode("utf-8")).get("ok") else None
        except Exception:
            return None
    # كلهم مع بعض: ويندوز بياخد وقت مع كل بورت مقفول، فواحد ورا التاني كان بياخد ~12 ثانية
    with ThreadPoolExecutor(max_workers=len(ports)) as ex:
        found = list(ex.map(check, ports))
    return next((u for u in found if u), None)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = running_url()
    if url:
        print(f"Markazi شغال بالفعل ← بفتح {url}")
        try:
            os.startfile(url)
        except Exception:
            webbrowser.open(url)
        return 0
    print("Markazi مش شغال ← بشغّله في وضع التطوير (هيفتح المتصفح لوحده)... لإيقافه: Ctrl+C أو اقفل التيرمنال.")
    return subprocess.call([sys.executable, str(ROOT / "run.py"), "--dev"], cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
