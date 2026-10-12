# -*- coding: utf-8 -*-
"""Install a skill's pinned Python dependencies inside the installer sandbox.

``python3 -I -B kvist_deps.py --target DIR --proxy-socket SOCK --proxy-port N -- PIN...``
creates a throw-away virtual environment with the standard library's own pip
(ensurepip, offline), then installs exactly the pinned wheels into DIR:
binary wheels only, so no package's build or install script runs; no
dependency resolution beyond the listed pins; no bytecode written. The only
network path is the bridge to the core's proxy, which allows the package
index and nothing else.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kvist_netbridge import start_tcp_to_unix_bridge  # noqa: E402


def main(argv: list[str]) -> int:
    split = argv.index("--")
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--proxy-socket", required=True)
    parser.add_argument("--proxy-port", type=int, required=True)
    args = parser.parse_args(argv[:split])
    pins = argv[split + 1:]
    start_tcp_to_unix_bridge(args.proxy_port, args.proxy_socket)
    proxy = f"http://127.0.0.1:{args.proxy_port}"
    env = {**os.environ, "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "PIP_NO_INPUT": "1"}
    subprocess.run([sys.executable, "-I", "-m", "venv", "/tmp/kvist-pip"], check=True)
    command = [
        "/tmp/kvist-pip/bin/python", "-I", "-m", "pip", "install",
        "--no-deps", "--only-binary=:all:", "--no-compile", "--no-cache-dir",
        "--disable-pip-version-check", "--target", args.target, "--proxy", proxy, *pins,
    ]
    return subprocess.run(command, env=env).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
