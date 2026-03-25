"""Server entry point."""
from __future__ import annotations

import os
from http.server import HTTPServer

from rascal.app import AppHandler


def run(host: str = "0.0.0.0", port: int | None = None):
    """Start the server."""
    port = port or int(os.environ.get("PORT", "8080"))
    server = HTTPServer((host, port), AppHandler)
    print(f"Listening on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
