# -*- coding: utf-8 -*-
"""A real kvist-web beacon and HTTP server on loopback, for LiveServerTests. Prints `READY <beacon> <http>`."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from kvist_web import lan
from kvist_web.devices import DeviceStore
from kvist_web.http_api import ControllerHTTPServer
from kvist_web.pairing import Pairing


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="kvist-live-"))
    devices = DeviceStore(home / "devices.json")
    # The owner's window is replaced by a yes: the question and the token are still the real ones.
    pairing = Pairing(devices, asker=lambda title, text: True, start=lambda work: work())
    server = ControllerHTTPServer(("127.0.0.1", 0), SimpleNamespace(), admin_token="admin", lan=True, devices=devices, pairing=pairing)
    port = server.server_address[1]
    who = {"service": "kvist-web", "version": lan.BEACON_VERSION, "id": "c" * 32, "name": "live-test", "os": "linux", "os_name": "Test OS 1", "user": "tester", "port": port}
    beacon = lan.Beacon(port, port=0, who=who)
    beacon.start()
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    print(f"READY {beacon.port} {port}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
