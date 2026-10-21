# -*- coding: utf-8 -*-
"""A local stand-in for the GitHub release the installer downloads from.

The repository is private, so ``curl ... | bash`` cannot be run end to end. This serves the same
file names under the same path, from the working tree:

    python scripts/release/mock_release_server.py [--port 8765]
    curl -fsSL http://127.0.0.1:8765/davud-d/Kvist/releases/download/latest-plugin-build/install.sh \\
        | KVIST_RELEASE_URL=http://127.0.0.1:8765/davud-d/Kvist/releases/download/latest-plugin-build bash

Node.js and ffmpeg still come from their public servers: they are not part of the release.
"""
from __future__ import annotations

import argparse
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_PATH = "/davud-d/Kvist/releases/download/latest-plugin-build"
CHUNK = 1 << 20

# Release asset name -> where the working tree builds it (first existing file wins).
ASSETS: dict[str, tuple[Path, ...]] = {
    "install.sh": (ROOT / "scripts/release/install.sh",),
    "kvist-linux-x64": (ROOT / "dist/kvist",),
    "kvist-jetbrains.zip": (ROOT / "dist/jetbrains-plugin.zip",),
    "kvist-vscode.vsix": tuple(sorted((ROOT / "frontends/ide/vscode").glob("*.vsix"))),
}
_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def find_asset(name: str) -> Path | None:
    return next((path for path in ASSETS.get(name, ()) if path.is_file()), None)


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:  # noqa: N802 (http.server's naming)
        self._serve(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(send_body=True)

    def _serve(self, send_body: bool) -> None:
        prefix = RELEASE_PATH + "/"
        asset = find_asset(self.path[len(prefix):]) if self.path.startswith(prefix) else None
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND, "no such release asset")
            return
        size = asset.stat().st_size
        start, end = self._wanted_range(size)
        partial = (start, end) != (0, size - 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if send_body:
            self._copy(asset, start, end)

    def _wanted_range(self, size: int) -> tuple[int, int]:
        """curl probes with ``-r`` and resumes with it, so a range is honoured; anything odd is the whole file."""
        match = _RANGE.match(self.headers.get("Range", ""))
        if not match or not (match.group(1) or match.group(2)):
            return 0, size - 1
        if not match.group(1):
            return max(0, size - int(match.group(2))), size - 1
        start = int(match.group(1))
        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        return (start, end) if start <= end else (0, size - 1)

    def _copy(self, path: Path, start: int, end: int) -> None:
        left = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while left > 0:
                block = handle.read(min(CHUNK, left))
                if not block:
                    return
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(block)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def make_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the installer's release assets from this checkout.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    url = f"http://127.0.0.1:{server.server_port}{RELEASE_PATH}"
    for name in ASSETS:
        path = find_asset(name)
        print(f"{'ok     ' if path else 'MISSING'} {name}" + (f"  <- {path.relative_to(ROOT)}" if path else ""))
    print(f"\nRun the installer against it:\n  curl -fsSL {url}/install.sh | KVIST_RELEASE_URL={url} bash\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
