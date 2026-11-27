# -*- coding: utf-8 -*-
"""What the tools do: one working tab, its overlay, and batches of hand-like input.

The model sees pages as text snapshots with refs (``e12``); refs stay valid in
a document until it changes, so a batch of steps can use refs from one look.
Input reaches the page as real, trusted events: the pointer travels a curved
path at hand speed, buttons and keys are held for a moment, letters arrive one
by one. The overlay draws the same pointer the page receives.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from kernel.browser_mcp import endpoint
from kernel.browser_mcp.drivers import DriverError
from kernel.browser_mcp.keys import parse_chord
from kernel.browser_mcp.motion import STEP_MS, path

PAGE_JS = (Path(__file__).parent / "page" / "kvist_page.js").read_text(encoding="utf-8")
PRELOAD_JS = PAGE_JS + "\nif (window === window.top) globalThis.__kvist.mount();\n"
MAX_STEPS = 40
MAX_WAIT_MS = 15000
HUMAN_TYPING_LIMIT = 200  # longer text is inserted at once: a minute of typing would outlast the call
SNAPSHOT_LIMIT = 30000
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(?!\d)")
STOPPED = ("The person pressed Stop in the browser. Do not use the browser again in this task "
           "unless they ask you to; tell them where you stopped.")


class ToolError(Exception):
    """Goes back to the model as the tool's error text."""


class StepError(Exception):
    """One step of a batch failed; the batch stops there."""


def _pause(low: float, high: float) -> None:
    time.sleep(random.uniform(low, high))


def normalize_url(url: str) -> str:
    url = url.strip()
    if url == "about:blank":
        return url
    # "localhost:8080" is a host and port; "javascript:…" or "file:…" is a scheme of its own.
    if not _SCHEME.match(url):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError(f"only http(s) addresses can be opened, got {url!r}")
    return url


class Controller:
    def __init__(self, spec: str = "", connect: Callable[[str], Any] = endpoint.connect) -> None:
        self._spec = spec
        self._connect = connect
        self._driver: Any = None
        self.tab = ""
        self._preload = ""
        self._aliases: dict[str, str] = {}
        self._ids: dict[str, str] = {}
        self.cursor: tuple[float, float] | None = None
        self.stopped = False
        self.cancel = threading.Event()
        self._mac = False

    # --- connection and tabs ------------------------------------------------------------

    @property
    def driver(self) -> Any:
        if self._driver is None:
            try:
                self._driver = self._connect(self._spec)
            except endpoint.EndpointError as error:
                raise ToolError(str(error)) from None
        return self._driver

    def _alias(self, tab: str) -> str:
        if tab not in self._aliases:
            alias = f"t{len(self._aliases) + 1}"
            self._aliases[tab] = alias
            self._ids[alias] = tab
        return self._aliases[tab]

    def _resolve(self, alias: str) -> str:
        tab = self._ids.get(alias.strip())
        if tab is None or tab not in {item.id for item in self.driver.tabs()}:
            raise ToolError(f"no tab {alias!r}; browser_tabs lists the open ones")
        return tab

    def _eval(self, tab: str, expression: str, *, mount: bool = False, timeout: float = 15.0) -> Any:
        source = ("(async () => {\n" + PAGE_JS + "\nconst k = globalThis.__kvist;" + (" k.mount();" if mount else "")
                  + f"\nreturn JSON.stringify(await ({expression}));\n}})()")
        value = self.driver.evaluate(tab, source, timeout)
        return json.loads(value) if isinstance(value, str) else None

    def _work(self, expression: str, timeout: float = 15.0) -> Any:
        return self._eval(self.tab, expression, mount=True, timeout=timeout)

    def _focused_tab(self, tabs: list[Any]) -> str:
        best, best_score = tabs[0].id, -1
        for item in tabs:
            try:
                score = self.driver.evaluate(item.id, "String((document.visibilityState === 'visible' ? 1 : 0)"
                                             " + (document.hasFocus() ? 2 : 0))", 2.0)
            except DriverError:
                continue
            if score is not None and int(score) > best_score:
                best, best_score = item.id, int(score)
        return best

    def ensure_tab(self) -> str:
        if self.stopped:
            raise ToolError(STOPPED)
        tabs = self.driver.tabs()
        if self.tab and any(item.id == self.tab for item in tabs):
            self._check_stop()
            return self.tab
        self.tab = ""
        target = self._focused_tab(tabs) if tabs else self.driver.new_tab("about:blank")
        self._set_working(target)
        return target

    def _set_working(self, tab: str) -> None:
        if self.tab and self.tab != tab:
            self._release(self.tab)
        self.driver.activate(tab)
        self._preload = self.driver.add_preload(tab, PRELOAD_JS)
        self.tab = tab
        info = self._settle()
        self._mac = bool(info.get("mac"))
        self.cursor = (info.get("w", 800) / 2, info.get("h", 600) / 2)

    def _release(self, tab: str) -> None:
        try:
            if self._preload:
                self.driver.remove_preload(tab, self._preload)
            self._eval(tab, "k.unmount()", timeout=3.0)
        except DriverError:
            pass  # the tab is closed or navigating; its overlay goes with it
        self._preload = ""

    def _check_stop(self) -> None:
        try:
            stopped = self._eval(self.tab, "k.stopped", timeout=5.0)
        except DriverError:
            return  # mid-navigation: the next call looks again
        if stopped:
            self.stopped = True
            self._release(self.tab)
            raise ToolError(STOPPED)

    def _settle(self, timeout: float = 12.0) -> dict[str, Any]:
        """Wait until the working tab's document has loaded far enough to read and click."""
        deadline = time.monotonic() + timeout
        info: dict[str, Any] = {}
        while time.monotonic() < deadline and not self.cancel.is_set():
            try:
                info = self._work("k.info()", timeout=5.0) or {}
            except DriverError:
                time.sleep(0.2)
                continue
            if info.get("ready") in ("interactive", "complete"):
                return info
            time.sleep(0.15)
        return info

    def shutdown(self) -> None:
        if self._driver is None:
            return
        if self.tab and not self.stopped:
            self._release(self.tab)
        self._driver.close()

    # --- tools ---------------------------------------------------------------------

    def status(self, en: str, ru: str) -> None:
        try:
            self._work(f"k.status({json.dumps(en)}, {json.dumps(ru)})", timeout=5.0)
        except DriverError:
            pass  # a label on the overlay is not worth failing the action for

    def list_tabs(self) -> str:
        tabs = self.driver.tabs()
        if not tabs:
            return f"{self.driver.product}: no open tabs."
        lines = [f"{self.driver.product} ({self.driver.protocol}), {len(tabs)} tabs:"]
        for item in tabs:
            mark = "  ← Kvist works here" if item.id == self.tab else ""
            lines.append(f"{self._alias(item.id)}  {item.title or '(untitled)'} — {item.url}{mark}")
        return "\n".join(lines)

    def open_tab(self, url: str) -> str:
        if self.stopped:
            raise ToolError(STOPPED)
        target = normalize_url(url) if url else ""
        tab = self.driver.new_tab(target)
        self._set_working(tab)
        if target:
            self.status(f"Opening {urlsplit(target).hostname}", f"Открываю {urlsplit(target).hostname}")
        return f"Opened {self._alias(tab)}; Kvist works in it now.\n{self._where()}"

    def close_tab(self, alias: str) -> str:
        tab = self._resolve(alias) if alias else self.ensure_tab()
        if tab == self.tab:
            self._release(tab)
            self.tab = ""
        self.driver.close_tab(tab)
        return f"Closed {self._alias(tab)}."

    def switch_tab(self, alias: str) -> str:
        if self.stopped:
            raise ToolError(STOPPED)
        self._set_working(self._resolve(alias))
        return f"Kvist works in {self._alias(self.tab)} now.\n{self._where()}"

    def navigate(self, url: str, history: str) -> str:
        self.ensure_tab()
        if history:
            scripts = {"back": "history.back()", "forward": "history.forward()", "reload": "location.reload()"}
            if history not in scripts:
                raise ToolError("history is back, forward or reload")
            self.status({"back": "Going back", "forward": "Going forward", "reload": "Reloading"}[history],
                        {"back": "Назад", "forward": "Вперёд", "reload": "Обновляю"}[history])
            self._work(f"(setTimeout(() => {scripts[history]}, 50), null)")
            time.sleep(0.4)
        else:
            target = normalize_url(url)
            self.status(f"Opening {urlsplit(target).hostname}", f"Открываю {urlsplit(target).hostname}")
            self.driver.navigate(self.tab, target)
            time.sleep(0.3)
        self._settle()
        return self._where()

    def snapshot(self, full: bool) -> str:
        self.ensure_tab()
        self.status("Looking at the page", "Смотрю на страницу")
        return self._work(f"k.snapshot({json.dumps(bool(full))}, {SNAPSHOT_LIMIT})", timeout=30.0)

    def page_text(self, max_chars: int) -> str:
        self.ensure_tab()
        self.status("Reading the page", "Читаю страницу")
        info = self._work("k.info()")
        text = self._work(f"k.text({max(500, min(max_chars, 200000))})", timeout=30.0)
        return f"Page: {info.get('title', '')}\nURL: {info.get('url', '')}\n\n{text}"

    def screenshot(self) -> bytes:
        self.ensure_tab()
        self.driver.activate(self.tab)
        # The picture is of the page, not of Kvist's own frame, pill and cursor.
        self._work("k.hide(true)")
        try:
            time.sleep(0.05)
            return self.driver.screenshot(self.tab)
        finally:
            self._work("k.hide(false)")

    def _where(self) -> str:
        info = self._settle(timeout=3.0)
        return f"Now: {info.get('title', '')} — {info.get('url', '')}"

    # --- the batch -------------------------------------------------------------------

    def act(self, steps: list[Any]) -> tuple[str, bool]:
        """Run steps in order; stop at the first failure. Returns the report and whether all succeeded."""
        if not isinstance(steps, list) or not steps:
            raise ToolError("steps must be a non-empty list")
        if len(steps) > MAX_STEPS:
            raise ToolError(f"at most {MAX_STEPS} steps per call")
        self.ensure_tab()
        self.driver.activate(self.tab)
        done: list[str] = []
        for index, step in enumerate(steps, 1):
            if self.cancel.is_set():
                return "\n".join(done + [f"Cancelled before step {index}."]), False
            try:
                if index > 1:
                    self._check_stop()
                done.append(f"{index}. {self._step(step)}")
            except (StepError, DriverError, ValueError) as error:
                rest = f"; steps {index + 1}–{len(steps)} were not run" if index < len(steps) else ""
                done.append(f"{index}. FAILED: {error}{rest}")
                return "\n".join(done + [self._where()]), False
        return "\n".join(done + [self._where()]), True

    def _step(self, step: Any) -> str:
        if not isinstance(step, dict):
            raise StepError(f"a step is an object such as {{\"click\": \"e12\"}}, got {step!r}")
        kinds = [name for name in ("click", "hover", "drag", "type", "press", "scroll", "wait", "wait_for") if name in step]
        if len(kinds) != 1:
            raise StepError(f"each step has exactly one of click, hover, drag, type, press, scroll, wait, wait_for: {step!r}")
        handler: Callable[[dict[str, Any]], str] = getattr(self, f"_do_{kinds[0]}")
        return handler(step)

    def _point(self, target: Any) -> tuple[float, float, str]:
        if isinstance(target, dict) and "x" in target and "y" in target:
            target = [target["x"], target["y"]]
        if isinstance(target, list) and len(target) == 2 and all(isinstance(value, (int, float)) for value in target):
            x, y = float(target[0]), float(target[1])
            info = self._work("k.info()")
            if not (0 <= x <= info["w"] and 0 <= y <= info["h"]):
                raise StepError(f"({x:.0f}, {y:.0f}) is outside the {info['w']}×{info['h']} viewport")
            return x, y, self._work(f"k.at({x}, {y})")
        if not isinstance(target, str):
            raise StepError(f"a target is a ref like \"e12\" or [x, y], got {target!r}")
        ref = json.dumps(target.strip())
        for attempt in range(4):
            found = self._work(f"k.target({ref})")
            if "error" in found:
                raise StepError(found["error"])
            if "x" in found:
                return found["x"], found["y"], found["label"]
            if attempt < 2:
                self._wheel_by(*found["scroll"])
            else:
                self._work(f"k.reveal({ref})")
                time.sleep(0.15)
        raise StepError(f"{target} could not be brought into view")

    def _move_to(self, x: float, y: float) -> None:
        points = path(self.cursor or (x, y), (x, y))
        rounded = [[round(px, 1), round(py, 1)] for px, py in points]
        self._work(f"k.glide({json.dumps(rounded)}, {STEP_MS})")
        self.driver.move(self.tab, points, self.cancel.is_set)
        self.cursor = (x, y)

    def _wheel_by(self, dx: float, dy: float) -> None:
        x, y = self.cursor or (100.0, 100.0)
        remaining_x, remaining_y = dx, dy
        while abs(remaining_x) >= 1 or abs(remaining_y) >= 1:
            step_x = max(-120.0, min(120.0, remaining_x))
            step_y = max(-120.0, min(120.0, remaining_y))
            self.driver.wheel(self.tab, x, y, step_x, step_y)
            remaining_x -= step_x
            remaining_y -= step_y
            _pause(0.025, 0.05)
        time.sleep(0.2)

    def _press_button(self, x: float, y: float, button: str, clicks: int, hold_ms: float) -> None:
        self._work(f"k.point({x}, {y}, true)")
        self.driver.button(self.tab, x, y, True, button, clicks)
        if hold_ms:
            time.sleep(hold_ms / 1000)
        else:
            _pause(0.05, 0.11)
        self.driver.button(self.tab, x, y, False, button, clicks)
        self._work(f"k.point({x}, {y}, false)")

    def _do_click(self, step: dict[str, Any]) -> str:
        button = step.get("button", "left")
        if button not in ("left", "middle", "right"):
            raise StepError("button is left, middle or right")
        count = int(step.get("count", 1))
        hold_ms = min(float(step.get("hold_ms", 0)), MAX_WAIT_MS)
        if count not in (1, 2, 3):
            raise StepError("count is 1, 2 or 3")
        x, y, label = self._point(step["click"])
        self.status(f"Clicking {label}", f"Нажимаю {label}")
        self._move_to(x, y)
        _pause(0.06, 0.14)
        for click in range(1, count + 1):
            self._press_button(x, y, button, click, hold_ms)
            if click < count:
                _pause(0.06, 0.1)
        self._after_input()
        kind = {1: "clicked", 2: "double-clicked", 3: "triple-clicked"}[count]
        held = f", held {hold_ms:.0f} ms" if hold_ms else ""
        return f"{kind} {label} at ({x:.0f}, {y:.0f}){held}" + ("" if button == "left" else f" with the {button} button")

    def _do_hover(self, step: dict[str, Any]) -> str:
        x, y, label = self._point(step["hover"])
        self.status(f"Pointing at {label}", f"Навожу на {label}")
        self._move_to(x, y)
        time.sleep(0.25)
        return f"pointer on {label} at ({x:.0f}, {y:.0f})"

    def _do_drag(self, step: dict[str, Any]) -> str:
        if "to" not in step:
            raise StepError("drag needs \"to\": a ref or [x, y]")
        x, y, label = self._point(step["drag"])
        self.status(f"Dragging {label}", f"Перетаскиваю {label}")
        self._move_to(x, y)
        _pause(0.06, 0.12)
        self._work(f"k.point({x}, {y}, true)")
        self.driver.button(self.tab, x, y, True, "left", 1)
        time.sleep(min(float(step.get("hold_ms", 150)), MAX_WAIT_MS) / 1000)
        to_x, to_y, to_label = self._point(step["to"])
        self._move_to(to_x, to_y)
        _pause(0.08, 0.15)
        self.driver.button(self.tab, to_x, to_y, False, "left", 1)
        self._work(f"k.point({to_x}, {to_y}, false)")
        self._after_input()
        return f"dragged {label} to {to_label} ({to_x:.0f}, {to_y:.0f})"

    def _do_type(self, step: dict[str, Any]) -> str:
        text = step["type"]
        if not isinstance(text, str):
            raise StepError("type takes a string")
        where = ""
        if step.get("into") is not None:
            x, y, label = self._point(step["into"])
            self._move_to(x, y)
            _pause(0.06, 0.14)
            self._press_button(x, y, "left", 1, 0)
            where = f" into {label}"
        if step.get("clear"):
            self.driver.press(self.tab, parse_chord("Meta+A" if self._mac else "Ctrl+A"))
            self.driver.press(self.tab, parse_chord("Backspace"))
        self.status("Typing", "Печатаю")
        if len(text) > HUMAN_TYPING_LIMIT:
            self.driver.insert_text(self.tab, text)
        else:
            pauses = [random.uniform(0.035, 0.11) + (random.uniform(0.1, 0.25) if random.random() < 0.06 else 0.0)
                      for _ in text]
            self.driver.type_text(self.tab, text, pauses)
        return f"typed {len(text)} characters{where}" + (" (cleared first)" if step.get("clear") else "")

    def _do_press(self, step: dict[str, Any]) -> str:
        chord = parse_chord(str(step["press"]))
        hold_ms = min(float(step.get("hold_ms", 0)), MAX_WAIT_MS)
        repeat = int(step.get("repeat", 1))
        if not 1 <= repeat <= 50:
            raise StepError("repeat is 1 to 50")
        self.status(f"Pressing {step['press']}", f"Нажимаю {step['press']}")
        for count in range(repeat):
            self.driver.press(self.tab, chord, hold_ms / 1000)
            if count < repeat - 1:
                _pause(0.05, 0.11)
        self._after_input()
        times = f" ×{repeat}" if repeat > 1 else ""
        held = f", held {hold_ms:.0f} ms" if hold_ms else ""
        return f"pressed {step['press']}{times}{held}"

    def _do_scroll(self, step: dict[str, Any]) -> str:
        direction = step["scroll"]
        if direction not in ("down", "up", "left", "right"):
            raise StepError("scroll is down, up, left or right")
        if step.get("at") is not None:
            x, y, _label = self._point(step["at"])
            self._move_to(x, y)
        info = self._work("k.info()")
        amount = max(0.1, min(float(step.get("screens", 0.8)), 10.0))
        span = (info["h"] if direction in ("down", "up") else info["w"]) * amount
        sign = 1 if direction in ("down", "right") else -1
        self.status("Scrolling", "Листаю")
        self._wheel_by(sign * span if direction in ("left", "right") else 0, sign * span if direction in ("down", "up") else 0)
        return f"scrolled {direction} {amount:g} screen(s)"

    def _do_wait(self, step: dict[str, Any]) -> str:
        ms = max(0.0, min(float(step["wait"]), MAX_WAIT_MS))
        self.status("Waiting", "Жду")
        if self.cancel.wait(ms / 1000):
            raise StepError("cancelled")
        return f"waited {ms:.0f} ms"

    def _do_wait_for(self, step: dict[str, Any]) -> str:
        text = str(step["wait_for"])
        ms = max(100.0, min(float(step.get("timeout_ms", 10000)), MAX_WAIT_MS))
        self.status("Waiting", "Жду")
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            try:
                if self._work(f"k.waitText({json.dumps(text)}, {min(2000.0, ms):.0f})", timeout=ms / 1000 + 5):
                    return f"{text!r} is on the page"
            except DriverError:
                time.sleep(0.2)  # the page is between documents
        raise StepError(f"{text!r} did not appear within {ms:.0f} ms")

    def _after_input(self) -> None:
        """A click or key may start loading another page; the next step waits for it to become usable."""
        time.sleep(0.12)
        try:
            info = self._work("k.info()", timeout=3.0)
        except DriverError:
            info = {}
        if not info or info.get("ready") == "loading":
            self._settle()
