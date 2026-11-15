"""``kvist-web`` entrypoint."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .controller import WebController
from .http_api import ControllerHTTPServer, client_root, resolve_folder
from .instance import InstanceDescriptor, controller_home
from .installers import installer_for
from . import lan
from .devices import DeviceStore
from .pairing import Pairing
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
    parser.add_argument(
        "--lan", action="store_true",
        help="Serve phones on the local network: discovery beacon, private addresses only, a phone is trusted once the owner allows it.",
    )
    parser.add_argument("--auto-exit", action="store_true", help="With --lan: stop when no agent has been running for a minute.")
    parser.add_argument("--forget-phones", action="store_true", help="Stop trusting every phone.")
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
    if args.forget_phones:
        print(f"Forgot {DeviceStore().forget_all()} phone(s).")
        return
    if args.auto_exit and not args.lan:
        raise SystemExit("error: --auto-exit works only together with --lan")
    if args.lan:
        # A specific --host is kept, so phones can be served on one adapter only.
        if args.host == DEFAULT_HOST:
            args.host = "0.0.0.0"
        args.no_open = True
    if descriptor.health():
        value = descriptor.read()
        url = f"http://{value['host']}:{value['port']}"
        if args.lan and not descriptor.lan():
            raise SystemExit(
                f"error: Kvist web is already running at {url} without --lan; "
                "stop it with `kvist-web --stop`, then start it again with --lan"
            )
        if args.lan:
            print(f"Kvist web is already visible to phones ({url}).")
            return
        if not args.no_open:
            webbrowser.open(url)
        print(f"Kvist web is already running at {url}.")
        return
    if not args.lan and not _loopback(args.host) and not (args.allow_remote and args.access_token):
        raise SystemExit(
            "error: non-loopback bind requires both --allow-remote and --access-token"
        )
    controller = WebController(args.workspace)
    token = descriptor.token()
    russian = os.environ.get("LANG", "").lower().startswith("ru")
    devices = DeviceStore() if args.lan else None
    try:
        server = _listen(args, controller, token, devices, Pairing(devices, russian=russian) if devices else None)
    except OSError as error:
        if descriptor.health():
            raise SystemExit("Kvist web is already running.") from None
        raise SystemExit(f"error: cannot listen on {args.host}:{args.port}: {error}") from error
    port = server.server_address[1]
    if args.lan and not args.auto_exit:
        # Started by hand: the folder it was started in is the one for phones. A controller a core started
        # shows the folders of the agents that run, and none of its own.
        server.fixed_folders.add(resolve_folder(args.workspace))
    descriptor.write(args.host, port, token)
    _LOG.info(
        "controller started",
        extra={"component": "web", "workspace": args.workspace, "connection": args.host},
    )
    controller.restore_remote_connections()
    url = f"http://{args.host}:{port}"
    if not args.no_open:
        threading.Timer(0.15, lambda: webbrowser.open(url)).start()
    print(f"Kvist web is running at {url}")
    beacon = _start_beacon(port, russian) if args.lan else None
    if args.lan:
        threading.Thread(target=_watch, args=(server, args.auto_exit), name="kvist-watch", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        if beacon:
            beacon.close()
        server.server_close()
        controller.close()
        descriptor.clear_if_owner()
        _LOG.info("controller stopped", extra={"component": "web"})


# Several users of one computer each run a controller: the next free port is theirs.
PORT_TRIES = 10
IDLE_EXIT_SECONDS = 60.0


def _listen(args, controller, token, devices, pairing) -> ControllerHTTPServer:
    ports = range(args.port, args.port + PORT_TRIES) if args.lan else (args.port,)
    error: OSError | None = None
    for port in ports:
        try:
            return ControllerHTTPServer(
                (args.host, port), controller, admin_token=token, external_token=args.access_token,
                lan=args.lan, devices=devices, pairing=pairing,
            )
        except OSError as caught:
            error = caught
    assert error is not None
    raise error


def _interface_present() -> bool:
    try:
        return (client_root() / "mobile" / "index.html").is_file()
    except RuntimeError:
        return False


def _watch(server: ControllerHTTPServer, auto_exit: bool) -> None:
    """Stop when there is no reason to stay: the interface files are gone, or (with --auto-exit) no agent runs.

    The cores that started this controller keep a connection to it; when the last one is gone it has no reason
    to stay. A controller whose own files vanished (its program was replaced under it) serves nothing, and the
    cores start a sound one in its place."""
    while True:
        time.sleep(5.0)
        if not _interface_present():
            _LOG.error("the interface files are gone, stopping", extra={"component": "web"})
            server.shutdown()
            return
        if auto_exit and server.presence.idle_seconds() > IDLE_EXIT_SECONDS:
            _LOG.info("no agent is running, stopping", extra={"component": "web"})
            server.shutdown()
            return


def _start_beacon(port: int, russian: bool) -> lan.Beacon | None:
    who = lan.identity(port)
    print(lan.banner(who, lan.local_address(), russian))
    beacon = lan.Beacon(port, who=who)
    try:
        beacon.start()
    except OSError as error:
        # Pairing by address still works, so this is a warning and not a failure.
        _LOG.warning("discovery beacon is off: %s", error, extra={"component": "lan"})
        print(
            f"Автопоиск выключен ({error}); введи адрес в приложении вручную."
            if russian else
            f"Discovery is off ({error}); enter the address in the app by hand."
        )
        return None
    return beacon


def _loopback(host: str) -> bool:
    try:
        return __import__("ipaddress").ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


if __name__ == "__main__":
    main()
