"""Локальный сервер для проверки веб-сборки из dist/.

    python tools/serve_web.py [порт]      → http://localhost:8600

Обычный `python -m http.server` на Windows отдаёт .mjs как text/plain
(так записано в реестре), и браузер отказывается запускать Python-движок.
"""

import functools
import http.server
import sys
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".wasm": "application/wasm",
        ".json": "application/json",
        ".woff2": "font/woff2",
    }


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8600
    handler = functools.partial(Handler, directory=str(DIST))
    with http.server.ThreadingHTTPServer(("", port), handler) as httpd:
        print(f"http://localhost:{port}  (Ctrl+C — остановить)", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
