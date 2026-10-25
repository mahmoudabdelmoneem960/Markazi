import os
import socket
import sys
import threading
import webbrowser

from distributor import config
from distributor import create_app


def open_url(url):
    try:
        if hasattr(os, "startfile"):
            os.startfile(url)
        else:
            webbrowser.open(url)
    except Exception:
        webbrowser.open(url)


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
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass
    app = create_app()
    port = free_port(config.PORT)
    if port != config.PORT:
        print(f"!! بورت {config.PORT} مشغول (غالبًا نسخة قديمة من السيستم لسه شغالة). اقفلها لو مش محتاجها.", flush=True)
    url = f"http://{'127.0.0.1' if config.HOST in ('0.0.0.0', '') else config.HOST}:{port}"
    print(f"Markazi (إصدار {config.VERSION}) شغال على {url}", flush=True)
    if config.OPEN_BROWSER:
        threading.Timer(1.0, lambda: open_url(url)).start()
    try:
        from waitress import serve
        serve(app, host=config.HOST, port=port, threads=8)
    except ImportError:
        app.run(host=config.HOST, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
