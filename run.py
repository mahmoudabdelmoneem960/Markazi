"""تشغيل السيستم: python run.py  (أو run.bat). بيفتح المتصفح على http://127.0.0.1:5000"""
import socket
import threading
import webbrowser

from distributor import config
from distributor import create_app


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
    app = create_app()
    port = free_port(config.PORT)
    if port != config.PORT:
        print(f"!! بورت {config.PORT} مشغول (غالبًا نسخة قديمة من السيستم لسه شغالة). اقفلها لو مش محتاجها.")
    url = f"http://{'127.0.0.1' if config.HOST in ('0.0.0.0', '') else config.HOST}:{port}"
    print(f"Markazi (إصدار {config.VERSION}) شغال على {url}")
    if config.OPEN_BROWSER:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        from waitress import serve
        serve(app, host=config.HOST, port=port, threads=8)
    except ImportError:
        app.run(host=config.HOST, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
