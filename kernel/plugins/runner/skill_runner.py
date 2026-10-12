# -*- coding: utf-8 -*-
"""Entry point of a skill runner inside its sandbox (plugin API v1).

Started by the core as ``python3 -I -B skill_runner.py --fd N ...``. Before the
skill's code is imported, this trusted part prepares what the sandbox offers:
the virtual screen (Xvfb + i3) when the skill was granted ``display``, and the
bridge from a loopback port to the core's proxy when it may use the network.
Then it imports the entrypoint, checks ``describe`` and ``execute`` and serves
calls: one at a time, on a worker thread, so cancel/pause messages from the
core are read while a call runs.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kvist_skill  # noqa: E402
import kvist_wire  # noqa: E402
from kvist_display import start_display  # noqa: E402
from kvist_netbridge import start_tcp_to_unix_bridge  # noqa: E402

_JSON_TYPES = (dict, list, str, int, float, bool, type(None))


class _Runner:
    def __init__(self, channel: kvist_wire.Channel) -> None:
        self.channel = channel
        self.plugin: Any = None
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self._current: dict[str, Any] | None = None
        self._stopping = threading.Event()

    # --- requests from the skill to the core ------------------------------

    def transport_for(self, call_id: str):
        def transport(method: str, params: dict[str, Any]) -> Any:
            return self.request(call_id, method, params)

        return transport

    def request(self, call_id: str, method: str, params: dict[str, Any]) -> Any:
        waiter = {"event": threading.Event(), "reply": None}
        with self._pending_lock:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = waiter
        self.channel.send({"kind": "request", "id": request_id, "call_id": call_id,
                           "method": method, "params": params})
        waiter["event"].wait()
        reply = waiter["reply"]
        if reply is None:
            raise kvist_skill.Cancelled("cancelled", "the runner is shutting down")
        if not reply.get("ok"):
            kvist_skill.raise_for_error(reply.get("error") or {})
        return reply.get("value")

    def _deliver(self, message: dict[str, Any]) -> None:
        with self._pending_lock:
            waiter = self._pending.pop(message.get("id"), None)
        if waiter is not None:
            waiter["reply"] = message
            waiter["event"].set()

    def _release_all(self) -> None:
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter["event"].set()

    # --- serving the core --------------------------------------------------

    def serve(self) -> None:
        while not self._stopping.is_set():
            try:
                message = self.channel.receive()
            except EOFError:
                break
            kind = message.get("kind")
            if kind not in kvist_wire.CORE_KINDS:
                raise kvist_wire.ProtocolError(f"unexpected message kind {kind!r}")
            if kind == "response":
                self._deliver(message)
            elif kind == "describe":
                self.channel.send({"kind": "describe_result", "value": self._describe()})
            elif kind == "execute":
                self._start_call(message)
            elif kind == "control":
                # Cancellation and pause are enforced by the core on every
                # request; the skill learns of them at its next request.
                continue
            elif kind == "shutdown":
                break
        self._stopping.set()
        self._release_all()

    def _describe(self) -> Any:
        value = self.plugin.describe()
        _require_json(value, "describe()")
        return value

    def _start_call(self, message: dict[str, Any]) -> None:
        if self._current is not None:
            self.channel.send({"kind": "result", "call_id": message.get("call_id"), "status": "error",
                               "error": kvist_wire.error_payload("busy", "a call is already running")})
            return
        self._current = message
        threading.Thread(target=self._run_call, args=(message,), name="kvist-skill-call", daemon=True).start()

    def _run_call(self, message: dict[str, Any]) -> None:
        call_id = str(message.get("call_id"))
        tool = str(message.get("tool"))
        context = kvist_skill.Context(self.transport_for(call_id), call_id=call_id, tool=tool)
        try:
            value = self.plugin.execute(tool, message.get("arguments") or {}, context)
            _require_json(value, "execute()")
            result = {"status": "success", "value": value}
            if isinstance(value, dict) and value.get("status") in ("partial", "error") and "value" in value:
                result = {"status": value["status"], "value": value["value"]}
        except kvist_skill.Cancelled as error:
            result = {"status": "cancelled", "error": kvist_wire.error_payload(error.code, error.message)}
        except kvist_skill.Paused as error:
            result = {"status": "paused", "error": kvist_wire.error_payload(error.code, error.message)}
        except kvist_skill.Denied as error:
            result = {"status": "denied", "error": kvist_wire.error_payload(error.code, error.message)}
        except kvist_skill.SkillError as error:
            result = {"status": "error", "error": kvist_wire.error_payload(error.code, error.message)}
        except Exception as error:  # noqa: BLE001 - any failure of skill code is that call's error result
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            result = {"status": "error", "error": kvist_wire.error_payload(type(error).__name__, str(error))}
        self._current = None
        try:
            self.channel.send({"kind": "result", "call_id": call_id, **result})
        except kvist_wire.ProtocolError as error:
            self.channel.send({"kind": "result", "call_id": call_id, "status": "error",
                               "error": kvist_wire.error_payload("result_too_large", str(error))})


def _require_json(value: Any, where: str, depth: int = 0) -> None:
    if depth > 40 or not isinstance(value, _JSON_TYPES):
        raise TypeError(f"{where} must return JSON values only (dict, list, str, int, float, bool, None)")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{where}: object keys must be strings")
            _require_json(item, where, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _require_json(item, where, depth + 1)


def _load_plugin(skill_dir: Path, entrypoint: str) -> Any:
    path = (skill_dir / entrypoint).resolve()
    if skill_dir.resolve() not in path.parents:
        raise RuntimeError("entrypoint is outside the skill package")
    sys.path.insert(0, str(skill_dir))
    spec = importlib.util.spec_from_file_location("kvist_skill_plugin", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("entrypoint cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("describe", "execute"):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(f"{entrypoint} must define a function {name}()")
    return module


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--skill", default="/kvist/skill")
    parser.add_argument("--entry", default="plugin.py")
    parser.add_argument("--site", action="append", default=[])
    parser.add_argument("--display", default="")
    parser.add_argument("--screen-dir", default="")
    parser.add_argument("--screen-size", default="1280x800")
    parser.add_argument("--proxy-socket", default="")
    parser.add_argument("--proxy-port", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    sock = socket.socket(fileno=args.fd)
    channel = kvist_wire.Channel(sock)
    try:
        for site in args.site:
            import site as site_module

            site_module.addsitedir(site)
        if args.display:
            start_display(args.display, args.screen_dir, args.screen_size)
        if args.proxy_socket and args.proxy_port:
            start_tcp_to_unix_bridge(args.proxy_port, args.proxy_socket)
            proxy = f"http://127.0.0.1:{args.proxy_port}"
            for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                os.environ[name] = proxy
            os.environ["NO_PROXY"] = os.environ["no_proxy"] = ""
        runner = _Runner(channel)
        runner.plugin = _load_plugin(Path(args.skill), args.entry)
    except Exception as error:  # noqa: BLE001 - reported to the core as a fatal start failure
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        channel.send({"kind": "fatal", "error": kvist_wire.error_payload(type(error).__name__, str(error))})
        return 2
    channel.send({"kind": "hello", "api": kvist_wire.PLUGIN_API_VERSION, "pid": os.getpid()})
    runner.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
