# -*- coding: utf-8 -*-
"""Run a task's server inside its sandbox and expose one of its ports as a unix socket.

``python3 -I -B kvist_service.py --unix PATH --port N -- COMMAND...`` starts
COMMAND, forwards each connection on the unix socket (bind-mounted from a
core-owned directory) to 127.0.0.1:N inside the sandbox's own network
namespace, and exits with COMMAND's status. The core's proxy is the only
client of that socket, so the server stays unreachable from the host's
loopback and from other sandboxes.
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kvist_netbridge import start_unix_to_tcp_bridge  # noqa: E402


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: kvist_service.py --unix PATH --port N -- COMMAND...", file=sys.stderr)
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser()
    parser.add_argument("--unix", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv[:split])
    command = argv[split + 1:]
    start_unix_to_tcp_bridge(args.unix, args.port)
    child = subprocess.Popen(command)

    def forward(signum, _frame) -> None:
        child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    return child.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
