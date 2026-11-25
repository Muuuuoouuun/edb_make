"""Loopback-only preview for PNG Maker. Run with python preview.py."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "web"


class Preview(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(404)
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()


if __name__ == "__main__":
    handler = partial(Preview, directory=str(ROOT))
    with ThreadingHTTPServer(("127.0.0.1", 18861), handler) as server:
        print("PNG Maker: http://127.0.0.1:18861", flush=True)
        server.serve_forever()
