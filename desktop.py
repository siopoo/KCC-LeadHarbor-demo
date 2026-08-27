from __future__ import annotations

import logging
import os
import threading
import webbrowser

from waitress import serve

from web_app import app


def open_browser() -> None:
    webbrowser.open("http://127.0.0.1:8765")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if os.getenv("KCC_LEADHARBOR_NO_BROWSER", "") != "1":
        threading.Timer(1.2, open_browser).start()
    serve(app, host="127.0.0.1", port=8765, threads=6)
