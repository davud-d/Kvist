"""``kvist-web`` entrypoint."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from .controller import WebController
from .http_api import ControllerHTTPServer
from .instance import InstanceDescriptor, controller_home
from .installers import installer_for
from .logging_setup import configure_logging


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9671
_LOG = logging.getLogger("kvist.web.controller")


def service_command() -> list[str]:
    """Return a stable controller command for native autostart.

    An installed wheel must use the exact interpreter that imported this
    package. Resolving ``kvist-web`` through PATH could otherwise retain an
    unrelated virtual environment or an older globally installed release.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--web-controller"]
    source_entry = Path(__file__).resolve().parents[3] / "kernel" / "__main__.py"
    if source_entry.is_file():
        return [sys.executable, str(source_entry), "--web-controller"]
    return [sys.executable, "-m", "kvist_web.cli"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kvist local web control center")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser.")
    parser.add_argument("--install-system", action="store_true")
    parser.add_argument("--uninstall-system", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--allow-remote", action="store_true", help="Allow a non-loopback bind.")
    parser.add_argument("--access-token", default="", help="Required for a non-loopback bind.")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("error: port must be between 1 and 65535")
    configure_logging(controller_home() / "logs" / "web.jsonl", verbose=args.verbose)
    descriptor = InstanceDescriptor()
    if args.install_system or args.uninstall_system:
        installer = installer_for(service_command())
        result = installer.install() if args.install_system else installer.uninstall()
        _LOG.info(
            result.message,
            extra={"component": "installer", "event": result.action},
        )
        print(result.message)
        print(result.target)
        return
    if args.status:
        value = descriptor.read()
        if descriptor.health():
            print(f"Kvist web is running at http://{value['host']}:{value['port']} (pid {value['pid']}).")
        else:
            print("Kvist web is not running.")
        return
    if args.stop:
        if descriptor.stop():
            print("Kvist web stopped.")
        else:
            print("Kvist web is not running.")
        return
    if descriptor.health():
        value = descriptor.read()
        url = f"http://{value['host']}:{value['port']}"
        if not args.no_open:
            webbrowser.open(url)
        print(f"Kvist web is already running at {url}.")
        return
    if not _loopback(args.host) and not (args.allow_remote and args.access_token):
        raise SystemExit(
            "error: non-loopback bind requires both --allow-remote and --access-token"
        )
    controller = WebController(args.workspace)
    token = descriptor.token()
    try:
        server = ControllerHTTPServer(
            (args.host, args.port),
            controller,
            admin_token=token,
            external_token=args.access_token,
        )
    except OSError as error:
        if descriptor.health():
            raise SystemExit("Kvist web is already running.") from None
        raise SystemExit(f"error: cannot listen on {args.host}:{args.port}: {error}") from error
    descriptor.write(args.host, args.port, token)
    _LOG.info(
        "controller started",
        extra={"component": "web", "workspace": args.workspace, "connection": args.host},
    )
    controller.restore_remote_connections()
    url = f"http://{args.host}:{args.port}"
    if not args.no_open:
        threading.Timer(0.15, lambda: webbrowser.open(url)).start()
    print(f"Kvist web is running at {url}")
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()
        descriptor.clear_if_owner()
        _LOG.info("controller stopped", extra={"component": "web"})


def _loopback(host: str) -> bool:
    try:
        return __import__("ipaddress").ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


if __name__ == "__main__":
    main()
