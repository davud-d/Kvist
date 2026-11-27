# -*- coding: utf-8 -*-
"""The two wire protocols behind one small driver API.

Chromium family: Chrome DevTools Protocol over the browser endpoint, one flat
session per tab. Firefox: WebDriver BiDi (Firefox removed CDP). Both run the
page helpers in an isolated world named ``kvist``: it shares the DOM with the
page but not its JavaScript, so page scripts can neither see nor fake it.
Expressions given to ``evaluate`` must produce a string (or a promise of one).
"""
from __future__ import annotations

import base64
import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from kernel.browser_mcp.keys import MODIFIERS, Chord, Key, char_key
from kernel.browser_mcp.motion import STEP_MS
from kernel.browser_mcp.websocket import WebSocket, WebSocketError

WORLD = "kvist"
CALL_TIMEOUT = 15.0
_BUTTONS = {"left": (0, 1), "middle": (1, 4), "right": (2, 2)}  # name: (WebDriver button, CDP buttons bit)


class DriverError(Exception):
    """The browser refused a command or the connection is gone."""


@dataclass(frozen=True, slots=True)
class Tab:
    id: str
    url: str
    title: str


class Rpc:
    """Numbered requests over one WebSocket; anything without a waiting id is an event."""

    def __init__(self, ws: WebSocket, on_event: Callable[[dict[str, Any]], None]) -> None:
        self._ws = ws
        self._on_event = on_event
        self._next = 0
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.error = ""
        threading.Thread(target=self._read_loop, name="kvist-browser-rpc", daemon=True).start()

    def call(self, message: dict[str, Any], timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        if self.error:
            raise DriverError(f"the browser connection is closed: {self.error}")
        with self._lock:
            self._next += 1
            request_id = self._next
            answer: queue.Queue = queue.Queue(maxsize=1)
            self._pending[request_id] = answer
        try:
            self._ws.send(json.dumps({"id": request_id, **message}))
            reply = answer.get(timeout=timeout)
        except WebSocketError as error:
            raise DriverError(str(error)) from None
        except queue.Empty:
            raise DriverError(f"{message.get('method')}: the browser did not answer in {timeout:.0f} s") from None
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if reply.get("_closed"):
            raise DriverError(f"the browser connection is closed: {self.error}")
        return reply

    def _read_loop(self) -> None:
        try:
            while True:
                message = json.loads(self._ws.recv())
                if not isinstance(message, dict):
                    continue
                with self._lock:
                    answer = self._pending.get(message.get("id"))
                if answer is not None:
                    answer.put(message)
                else:
                    self._on_event(message)
        except (WebSocketError, json.JSONDecodeError) as error:
            self.error = str(error) or "closed"
            with self._lock:
                waiting = list(self._pending.values())
            for answer in waiting:
                answer.put({"_closed": True})

    def close(self) -> None:
        self._ws.close()


class CdpDriver:
    protocol = "cdp"

    def __init__(self, ws_url: str, product: str = "") -> None:
        self.product = product
        self._sessions: dict[str, str] = {}  # target id -> session id
        self._buttons = 0
        self._rpc = Rpc(WebSocket(ws_url), self._on_event)
        if not self.product:
            self.product = self._call("Browser.getVersion").get("product", "")

    def _on_event(self, message: dict[str, Any]) -> None:
        if message.get("method") == "Target.detachedFromTarget":
            session = (message.get("params") or {}).get("sessionId")
            for target, known in list(self._sessions.items()):
                if known == session:
                    self._sessions.pop(target, None)

    def _call(self, method: str, params: dict[str, Any] | None = None, session: str = "",
              timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        message: dict[str, Any] = {"method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        reply = self._rpc.call(message, timeout)
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise DriverError(f"{method}: {error.get('message', 'failed')}")
        return reply.get("result") or {}

    def _session(self, tab: str) -> str:
        if tab not in self._sessions:
            result = self._call("Target.attachToTarget", {"targetId": tab, "flatten": True})
            self._sessions[tab] = result["sessionId"]
        return self._sessions[tab]

    def tabs(self) -> list[Tab]:
        infos = self._call("Target.getTargets").get("targetInfos") or []
        return [Tab(info["targetId"], info.get("url", ""), info.get("title", ""))
                for info in infos if info.get("type") == "page" and not info.get("url", "").startswith("devtools://")]

    def new_tab(self, url: str) -> str:
        return self._call("Target.createTarget", {"url": url or "about:blank"})["targetId"]

    def close_tab(self, tab: str) -> None:
        self._call("Target.closeTarget", {"targetId": tab})
        self._sessions.pop(tab, None)

    def activate(self, tab: str) -> None:
        self._call("Target.activateTarget", {"targetId": tab})

    def navigate(self, tab: str, url: str) -> None:
        result = self._call("Page.navigate", {"url": url}, self._session(tab))
        if result.get("errorText"):
            raise DriverError(f"navigation failed: {result['errorText']}")

    def evaluate(self, tab: str, expression: str, timeout: float = CALL_TIMEOUT) -> str:
        session = self._session(tab)
        frame = self._call("Page.getFrameTree", {}, session)["frameTree"]["frame"]["id"]
        # The same world name always resolves to the same world, the one the preload script ran in.
        context = self._call("Page.createIsolatedWorld", {"frameId": frame, "worldName": WORLD}, session)
        result = self._call("Runtime.evaluate", {
            "expression": expression, "contextId": context["executionContextId"],
            "returnByValue": True, "awaitPromise": True, "userGesture": False,
        }, session, timeout)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text", "")
            raise DriverError(f"page script failed: {text[:500]}")
        return (result.get("result") or {}).get("value")

    def add_preload(self, tab: str, source: str) -> str:
        result = self._call("Page.addScriptToEvaluateOnNewDocument",
                            {"source": source, "worldName": WORLD}, self._session(tab))
        return result["identifier"]

    def remove_preload(self, tab: str, handle: str) -> None:
        self._call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": handle}, self._session(tab))

    def move(self, tab: str, points: list[tuple[float, float]], cancelled: Callable[[], bool]) -> None:
        session = self._session(tab)
        start = time.monotonic()
        for index, (x, y) in enumerate(points, 1):
            if cancelled():
                return
            self._call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y,
                                                    "buttons": self._buttons}, session)
            delay = start + index * STEP_MS / 1000 - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def button(self, tab: str, x: float, y: float, pressed: bool, button: str, clicks: int) -> None:
        bit = _BUTTONS[button][1]
        self._buttons = self._buttons | bit if pressed else self._buttons & ~bit
        self._call("Input.dispatchMouseEvent", {
            "type": "mousePressed" if pressed else "mouseReleased", "x": x, "y": y, "button": button,
            "buttons": self._buttons, "clickCount": clicks,
        }, self._session(tab))

    def wheel(self, tab: str, x: float, y: float, dx: float, dy: float) -> None:
        self._call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": dx, "deltaY": dy},
                   self._session(tab))

    def _key_event(self, session: str, kind: str, key: Key, modifiers: int) -> None:
        params: dict[str, Any] = {"type": kind, "key": key.key, "code": key.code,
                                  "windowsVirtualKeyCode": key.vk, "modifiers": modifiers}
        # Ctrl/Alt/Meta chords are shortcuts, not typing.
        if kind == "keyDown" and key.text and not modifiers & 0b111:
            params["text"] = params["unmodifiedText"] = key.text
        self._call("Input.dispatchKeyEvent", params, session)

    def press(self, tab: str, chord: Chord, hold: float = 0.0) -> None:
        session = self._session(tab)
        bits = 0
        for name in chord.modifiers:
            key, code, bit, _ = MODIFIERS[name]
            bits |= bit
            self._call("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": key, "code": code, "modifiers": bits}, session)
        self._key_event(session, "keyDown", chord.key, bits)
        if hold:
            time.sleep(hold)
        self._key_event(session, "keyUp", chord.key, bits)
        for name in reversed(chord.modifiers):
            key, code, bit, _ = MODIFIERS[name]
            bits &= ~bit
            self._call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code, "modifiers": bits}, session)

    def type_text(self, tab: str, text: str, pauses: list[float]) -> None:
        session = self._session(tab)
        for char, pause in zip(text, pauses, strict=True):
            key = char_key(char)
            self._key_event(session, "keyDown", key, 0)
            self._key_event(session, "keyUp", key, 0)
            time.sleep(pause)

    def insert_text(self, tab: str, text: str) -> None:
        self._call("Input.insertText", {"text": text}, self._session(tab))

    def screenshot(self, tab: str) -> bytes:
        result = self._call("Page.captureScreenshot", {"format": "jpeg", "quality": 85}, self._session(tab), 30.0)
        return base64.b64decode(result["data"])

    def close(self) -> None:
        self._rpc.close()


class BidiDriver:
    protocol = "bidi"

    def __init__(self, ws_url: str) -> None:
        self._rpc = Rpc(WebSocket(ws_url), lambda _message: None)
        # "ignore": the person's own alerts and confirms must stay theirs to answer.
        result = self._call("session.new", {"capabilities": {"alwaysMatch": {
            "unhandledPromptBehavior": {"default": "ignore"}}}})
        capabilities = result.get("capabilities") or {}
        self.product = f"{capabilities.get('browserName', 'firefox')} {capabilities.get('browserVersion', '')}".strip()

    def _call(self, method: str, params: dict[str, Any], timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        reply = self._rpc.call({"method": method, "params": params}, timeout)
        if reply.get("type") == "error":
            raise DriverError(f"{method}: {reply.get('message') or reply.get('error')}")
        return reply.get("result") or {}

    def tabs(self) -> list[Tab]:
        contexts = self._call("browsingContext.getTree", {"maxDepth": 0}).get("contexts") or []
        tabs = []
        for context in contexts:
            try:
                title = self.evaluate(context["context"], "document.title", timeout=3.0) or ""
            except DriverError:
                title = ""
            tabs.append(Tab(context["context"], context.get("url", ""), title))
        return tabs

    def new_tab(self, url: str) -> str:
        tab = self._call("browsingContext.create", {"type": "tab"})["context"]
        if url:
            self.navigate(tab, url)
        return tab

    def close_tab(self, tab: str) -> None:
        self._call("browsingContext.close", {"context": tab})

    def activate(self, tab: str) -> None:
        self._call("browsingContext.activate", {"context": tab})

    def navigate(self, tab: str, url: str) -> None:
        self._call("browsingContext.navigate", {"context": tab, "url": url, "wait": "none"})

    def evaluate(self, tab: str, expression: str, timeout: float = CALL_TIMEOUT) -> str:
        result = self._call("script.evaluate", {
            "expression": expression, "target": {"context": tab, "sandbox": WORLD},
            "awaitPromise": True, "resultOwnership": "none",
        }, timeout)
        if result.get("type") == "exception":
            raise DriverError(f"page script failed: {(result.get('exceptionDetails') or {}).get('text', '')[:500]}")
        return (result.get("result") or {}).get("value")

    def add_preload(self, tab: str, source: str) -> str:
        result = self._call("script.addPreloadScript", {
            "functionDeclaration": "() => {\n" + source + "\n}", "sandbox": WORLD, "contexts": [tab]})
        return result["script"]

    def remove_preload(self, tab: str, handle: str) -> None:
        self._call("script.removePreloadScript", {"script": handle})

    def _perform(self, tab: str, source: dict[str, Any], timeout: float = 60.0) -> None:
        self._call("input.performActions", {"context": tab, "actions": [source]}, timeout)

    def move(self, tab: str, points: list[tuple[float, float]], cancelled: Callable[[], bool]) -> None:
        # Firefox paces the chain itself; each point is reached STEP_MS after the previous one.
        steps = [{"type": "pointerMove", "x": round(x), "y": round(y), "duration": STEP_MS} for x, y in points]
        self._perform(tab, {"type": "pointer", "id": "kvist-mouse", "parameters": {"pointerType": "mouse"},
                            "actions": steps})

    def button(self, tab: str, x: float, y: float, pressed: bool, button: str, clicks: int) -> None:
        self._perform(tab, {"type": "pointer", "id": "kvist-mouse", "parameters": {"pointerType": "mouse"},
                            "actions": [{"type": "pointerDown" if pressed else "pointerUp",
                                         "button": _BUTTONS[button][0]}]})

    def wheel(self, tab: str, x: float, y: float, dx: float, dy: float) -> None:
        self._perform(tab, {"type": "wheel", "id": "kvist-wheel", "actions": [
            {"type": "scroll", "x": round(x), "y": round(y), "deltaX": round(dx), "deltaY": round(dy)}]})

    def press(self, tab: str, chord: Chord, hold: float = 0.0) -> None:
        values = [MODIFIERS[name][3] for name in chord.modifiers]
        actions = [{"type": "keyDown", "value": value} for value in values]
        actions.append({"type": "keyDown", "value": chord.key.webdriver})
        if hold:
            actions.append({"type": "pause", "duration": round(hold * 1000)})
        actions.append({"type": "keyUp", "value": chord.key.webdriver})
        actions += [{"type": "keyUp", "value": value} for value in reversed(values)]
        self._perform(tab, {"type": "key", "id": "kvist-keys", "actions": actions})

    def type_text(self, tab: str, text: str, pauses: list[float]) -> None:
        actions: list[dict[str, Any]] = []
        for char, pause in zip(text, pauses, strict=True):
            value = char_key(char).webdriver
            actions += [{"type": "keyDown", "value": value}, {"type": "keyUp", "value": value},
                        {"type": "pause", "duration": round(pause * 1000)}]
        self._perform(tab, {"type": "key", "id": "kvist-keys", "actions": actions})

    def insert_text(self, tab: str, text: str) -> None:
        self.type_text(tab, text, [0.0] * len(text))

    def screenshot(self, tab: str) -> bytes:
        result = self._call("browsingContext.captureScreenshot",
                            {"context": tab, "format": {"type": "image/jpeg", "quality": 0.85}}, 30.0)
        return base64.b64decode(result["data"])

    def close(self) -> None:
        try:
            self._call("session.end", {}, timeout=3.0)
        except DriverError:
            pass  # the browser may already be gone; the socket is closed either way
        self._rpc.close()
