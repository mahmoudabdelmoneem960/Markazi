"""تشغيل السيرفر من مجلد backend: python run.py (أو run.bat). بيفتح المتصفح على http://127.0.0.1:5000"""
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

# إضافة مسار المشروع إلى sys.path إذا تم تشغيله مباشرة من الـ Terminal داخل مجلد backend
BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
for p in (str(BACKEND_DIR), str(ROOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from distributor import config, create_app


def free_port(start):
    for p in [start] + [x for x in range(5002, 5030) if x != start]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((config.HOST, p))
                return p
            except OSError:
                continue
    return start


def main():
    dev_mode = "--dev" in sys.argv or os.environ.get("SD_DEV", "").lower() in ("1", "true", "yes")
    app = create_app()
    port = free_port(config.PORT)
    if port != config.PORT:
        print(f"!! بورت {config.PORT} مشغول (غالبًا نسخة قديمة من السيستم لسه شغالة). اقفلها لو مش محتاجها.")
    url = f"http://{'127.0.0.1' if config.HOST in ('0.0.0.0', '') else config.HOST}:{port}"
    mode_tag = " [وضع التطوير والتحديث التلقائي Live-Reload]" if dev_mode else ""
    print(f"Markazi (إصدار {config.VERSION}) شغال على {url}{mode_tag}")
    if config.OPEN_BROWSER and not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    if dev_mode:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
        app.run(host=config.HOST, port=port, debug=True, use_reloader=True)
    else:
        try:
            from waitress import serve
            serve(app, host=config.HOST, port=port, threads=8)
        except ImportError:
            app.run(host=config.HOST, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
