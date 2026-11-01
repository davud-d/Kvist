# -*- coding: utf-8 -*-
"""Kvist app_screen skill: graphical programs on the skill's own virtual screen, driven through XTEST.

The runner started Xvfb and the window manager before importing this module (``DISPLAY`` is set). Nothing here
touches the person's desktop: the screen lives in the runner's sandbox. Input is sent with the X server's own
XTEST extension, so the pointer is a real X pointer and programs see ordinary events.
"""
from __future__ import annotations

import base64
import os
import random
import shutil
import struct
import subprocess
import time
from typing import Any

import motion

BUDGET_MIB = 40.0
MAX_TEXT = 4000
BUTTONS = {"left": 1, "middle": 2, "right": 3}
_MODIFIERS = {"ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L",
              "win": "Super_L", "meta": "Meta_L"}
_KEY_NAMES = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
              "backspace": "BackSpace", "del": "Delete", "delete": "Delete", "space": "space", "up": "Up",
              "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
              "pgup": "Prior", "pageup": "Prior", "pgdn": "Next", "pagedown": "Next", "insert": "Insert"}
_RNG = random.Random()


class ScreenError(RuntimeError):
    """A failure the model can act on; its text says what to change."""


class Screen:
    """The X connection, the pointer, the programs started here and the clipboard owner."""

    def __init__(self) -> None:
        self.dpy: Any = None
        self.apps: list[dict[str, Any]] = []
        self.glx = False
        self._clipboard: subprocess.Popen | None = None
        self._borrowed: dict[int, int] = {}
        self._free_keycodes: list[int] = []

    # --- connection ------------------------------------------------------

    def connect(self) -> Any:
        if self.dpy is not None:
            return self.dpy
        from Xlib import display

        self.dpy = display.Display()
        for extension in ("XTEST", "XFIXES"):
            if not self.dpy.has_extension(extension):
                raise ScreenError(f"the virtual screen has no {extension} extension")
        self.dpy.xfixes_query_version()
        return self.dpy

    def reconnect(self) -> None:
        if self.dpy is not None:
            from Xlib.error import ConnectionClosedError

            try:
                self.dpy.close()
            except (OSError, ConnectionClosedError):  # the server it was talking to is already gone
                pass
        self.dpy = None
        self._borrowed, self._free_keycodes = {}, []
        self.connect()

    @property
    def root(self) -> Any:
        return self.connect().screen().root

    def size(self) -> tuple[int, int]:
        geometry = self.root.get_geometry()
        return geometry.width, geometry.height

    # --- pointer ---------------------------------------------------------

    def pointer(self) -> tuple[int, int]:
        reply = self.root.query_pointer()
        return reply.root_x, reply.root_y

    def _fake(self, kind: int, detail: int = 0, x: int = 0, y: int = 0) -> None:
        self.connect().xtest_fake_input(kind, detail, 0, 0, x, y)

    def move(self, x: int, y: int, *, slow: bool = False) -> None:
        from Xlib import X

        width, height = self.size()
        if not (0 <= x < width and 0 <= y < height):
            raise ScreenError(f"({x}, {y}) is outside the screen ({width}x{height})")
        for px, py, delay in motion.pointer_path(self.pointer(), (x, y), _RNG, slow=slow):
            self._fake(X.MotionNotify, 0, px, py)
            self.dpy.sync()
            time.sleep(delay)
        if self.pointer() != (x, y):
            self._fake(X.MotionNotify, 0, x, y)
            self.dpy.sync()

    def button(self, number: int, pressed: bool) -> None:
        from Xlib import X

        self._fake(X.ButtonPress if pressed else X.ButtonRelease, number)
        self.dpy.sync()

    def click(self, number: int, count: int = 1) -> None:
        time.sleep(motion.settle_pause(_RNG))
        for index in range(count):
            if index:
                time.sleep(motion.double_click_gap(_RNG))
            self.button(number, True)
            time.sleep(motion.click_hold(_RNG))
            self.button(number, False)

    def scroll(self, dx: int, dy: int) -> None:
        for number, amount in ((4 if dy < 0 else 5, abs(dy)), (6 if dx < 0 else 7, abs(dx))):
            for _ in range(amount):
                self.button(number, True)
                self.button(number, False)
                time.sleep(_RNG.uniform(0.03, 0.11))

    # --- keyboard --------------------------------------------------------

    def _keysym_for(self, char: str) -> int:
        from Xlib import XK

        if char == "\n":
            return XK.string_to_keysym("Return")
        if char == "\t":
            return XK.string_to_keysym("Tab")
        code = ord(char)
        return code if code < 0x100 else 0x01000000 + code

    def _keycode_for_borrowed(self, keysym: int) -> int:
        """A free key of the layout holds a character the layout lacks, and keeps it for the session: a program
        that handles the key press a moment late must still find the character there, which giving the key
        back right after the press would not allow."""
        known = self._borrowed.get(keysym)
        if known is not None:
            return known
        d = self.connect()
        if not self._free_keycodes:
            low, high = d.display.info.min_keycode, d.display.info.max_keycode
            rows = d.get_keyboard_mapping(low, high - low + 1)
            self._free_keycodes = [code for code, row in zip(range(low, high + 1), rows) if not any(row)]
            if not self._free_keycodes:
                raise ScreenError("the keyboard layout has no free key to type this character with")
        if len(self._borrowed) >= len(self._free_keycodes):
            oldest = next(iter(self._borrowed))
            keycode = self._borrowed.pop(oldest)
        else:
            keycode = self._free_keycodes[len(self._borrowed)]
        width = len(d.get_keyboard_mapping(keycode, 1)[0])
        d.change_keyboard_mapping(keycode, [tuple([keysym] * width)])
        d.sync()
        time.sleep(0.03)
        self._borrowed[keysym] = keycode
        return keycode

    def tap_char(self, char: str) -> None:
        """Press the key that types ``char``."""
        from Xlib import X, XK

        d = self.connect()
        keysym = self._keysym_for(char)
        found = list(d.keysym_to_keycodes(keysym))
        if found:
            keycode, index = found[0]
            shifted = bool(index & 1)
        else:
            keycode, shifted = self._keycode_for_borrowed(keysym), False
        shift = d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        if shifted:
            self._fake(X.KeyPress, shift)
        self._fake(X.KeyPress, keycode)
        self._fake(X.KeyRelease, keycode)
        if shifted:
            self._fake(X.KeyRelease, shift)
        d.sync()

    def type_text(self, text: str) -> None:
        previous = ""
        for char in text:
            self.tap_char(char)
            time.sleep(motion.typing_delay(_RNG, previous, char))
            previous = char

    def press_combo(self, combo: str) -> None:
        """``ctrl+shift+v``, ``Return``, ``alt+F4``: modifiers down, the key tapped, modifiers up in reverse."""
        from Xlib import X, XK

        d = self.connect()
        parts = [part for part in combo.replace(" ", "").split("+") if part]
        if not parts:
            raise ScreenError("keys must not be empty")
        *modifiers, key = parts
        codes = []
        for name in modifiers:
            symbol = _MODIFIERS.get(name.lower())
            keycode = d.keysym_to_keycode(XK.string_to_keysym(symbol)) if symbol else 0
            if not keycode:
                raise ScreenError(f"unknown modifier {name!r}; use ctrl, shift, alt or super")
            codes.append(keycode)
        if len(key) == 1:
            main = None
        else:
            symbol = _KEY_NAMES.get(key.lower(), key)
            keysym = XK.string_to_keysym(symbol)
            main = d.keysym_to_keycode(keysym) if keysym else 0
            if not main:
                raise ScreenError(f"unknown key {key!r}; use a single character or a name such as Return, Tab, F5")
        for keycode in codes:
            self._fake(X.KeyPress, keycode)
        if main is None:
            self.tap_char(key.lower() if codes else key)
        else:
            self._fake(X.KeyPress, main)
            self._fake(X.KeyRelease, main)
        for keycode in reversed(codes):
            self._fake(X.KeyRelease, keycode)
        d.sync()

    def paste(self, text: str, combo: str) -> None:
        """Put ``text`` on the clipboard and press the paste keys: for text that key presses cannot carry
        (very long, or a program that ignores synthetic characters)."""
        if shutil.which("xclip") is None:
            raise ScreenError("xclip is not installed, so text cannot be pasted; type_text still works")
        self._release_clipboard()
        self._clipboard = subprocess.Popen(["xclip", "-selection", "clipboard", "-i"], stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert self._clipboard.stdin is not None
        self._clipboard.stdin.write(text.encode("utf-8"))
        self._clipboard.stdin.close()
        time.sleep(0.15)
        self.press_combo(combo)

    def _release_clipboard(self) -> None:
        if self._clipboard is not None and self._clipboard.poll() is None:
            self._clipboard.terminate()
        self._clipboard = None

    # --- windows ---------------------------------------------------------

    def windows(self) -> list[dict[str, Any]]:
        from Xlib import X

        d = self.connect()
        root = self.root
        listed = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        if listed is not None:
            candidates = [d.create_resource_object("window", wid) for wid in listed.value]
        else:  # no window manager, or one without EWMH: the root's mapped children are the windows
            candidates = [child for child in root.query_tree().children
                          if self._attributes(child) and child.get_attributes().map_state == X.IsViewable]
        return [info for info in (self._describe(window) for window in candidates) if info]

    @staticmethod
    def _attributes(window: Any) -> bool:
        try:
            return not window.get_attributes().override_redirect
        except Exception:  # noqa: BLE001 - python-xlib raises several error types for a window that just closed
            return False

    def _describe(self, window: Any) -> dict[str, Any] | None:
        from Xlib import X

        d = self.connect()
        try:
            geometry = window.get_geometry()
            origin = window.translate_coords(self.root, 0, 0)
            name = window.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
            title = name.value.decode("utf-8", "replace") if name is not None else (window.get_wm_name() or "")
            wm_class = window.get_wm_class() or ("", "")
            state = window.get_full_property(d.intern_atom("WM_STATE"), X.AnyPropertyType)
            pid = window.get_full_property(d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
        except Exception:  # noqa: BLE001 - see _attributes
            return None
        return {"id": window.id, "title": title[:200], "class": wm_class[-1], "x": -origin.x, "y": -origin.y,
                "width": geometry.width, "height": geometry.height,
                "minimized": bool(state is not None and state.value[0] == 3),
                "pid": int(pid.value[0]) if pid is not None else None}

    def _window(self, window_id: int) -> Any:
        if window_id not in {info["id"] for info in self.windows()}:
            raise ScreenError(f"no window {window_id} on the screen; call windows with op list")
        return self.connect().create_resource_object("window", window_id)

    def _tell_root(self, window: Any, message: str, data: list[int]) -> None:
        from Xlib import X
        from Xlib.protocol import event

        d = self.connect()
        padded = (data + [0] * 5)[:5]
        notice = event.ClientMessage(window=window, client_type=d.intern_atom(message), data=(32, padded))
        self.root.send_event(notice, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        d.sync()

    def focus(self, window_id: int) -> None:
        window = self._window(window_id)
        self._tell_root(window, "_NET_ACTIVE_WINDOW", [1, 0, 0])
        window.configure(stack_mode=0)  # Above: without a window manager nothing else raises it
        self.dpy.sync()

    def place(self, window_id: int, x: int | None, y: int | None, width: int | None, height: int | None) -> None:
        window = self._window(window_id)
        values = {key: value for key, value in (("x", x), ("y", y), ("width", width), ("height", height))
                  if value is not None}
        if not values:
            raise ScreenError("give at least one of x, y, width, height")
        window.configure(**values)
        self.dpy.sync()

    def minimize(self, window_id: int) -> None:
        self._tell_root(self._window(window_id), "WM_CHANGE_STATE", [3])

    def close(self, window_id: int) -> None:
        self._tell_root(self._window(window_id), "_NET_CLOSE_WINDOW", [0, 1])

    # --- cursor image, programs, memory ----------------------------------

    def cursor_image(self) -> dict[str, Any]:
        """The X server's current cursor and where it is, for the screenshot: Xvfb never paints it into the frame."""
        reply = self.connect().xfixes_get_cursor_image(self.root)
        pixels = struct.pack(f"<{len(reply.cursor_image)}I", *reply.cursor_image)
        return {"x": reply.x, "y": reply.y, "width": reply.width, "height": reply.height,
                "xhot": reply.xhot, "yhot": reply.yhot, "pixels": base64.b64encode(pixels).decode("ascii")}

    def launch(self, command: list[str], *, gl: bool) -> dict[str, Any]:
        executable = shutil.which(command[0])
        if executable is None:
            raise ScreenError(f"{command[0]!r} is not installed in the skill's sandbox")
        before = {info["id"] for info in self.windows()}
        # The sandbox's passwd entry names /usr/bin/false as the shell, which makes a terminal exit at once.
        environment = {**os.environ, "HOME": "/tmp/home", "DISPLAY": os.environ["DISPLAY"], "LANG": "C.UTF-8",
                       "SHELL": shutil.which("bash") or "/bin/sh", "TERM": "xterm-256color"}
        if gl:
            import kvist_display

            environment.update(kvist_display.software_gl_environment())
        process = subprocess.Popen([executable, *command[1:]], env=environment, cwd="/tmp/home",
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self.apps.append({"pid": process.pid, "command": command, "process": process})
        deadline = time.monotonic() + 6
        new: list[dict[str, Any]] = []
        while time.monotonic() < deadline and not new:
            if process.poll() is not None:
                raise ScreenError(f"{command[0]} exited at once with status {process.returncode}; for a terminal "
                                  "or another OpenGL program, launch again with gl=true")
            time.sleep(0.15)
            new = [info for info in self.windows() if info["id"] not in before]
        return {"pid": process.pid, "windows": new}

    def running_apps(self) -> list[dict[str, Any]]:
        self.apps = [app for app in self.apps if app["process"].poll() is None]
        return [{"pid": app["pid"], "command": app["command"]} for app in self.apps]

    def end_apps(self) -> int:
        stopped = 0
        for app in self.apps:
            if app["process"].poll() is None:
                app["process"].terminate()
                stopped += 1
        self.apps = []
        self._release_clipboard()
        return stopped


SCREEN = Screen()


# --- tools ---------------------------------------------------------------


def describe() -> dict[str, Any]:
    return {"tools": ["launch", "screenshot", "mouse", "keyboard", "windows", "apps", "status"]}


def execute(tool_name: str, arguments: dict[str, Any], ctx: Any) -> Any:
    handlers = {"launch": _launch, "screenshot": _screenshot, "mouse": _mouse, "keyboard": _keyboard,
                "windows": _windows, "apps": _apps, "status": _status}
    if tool_name not in handlers:
        raise ValueError(f"unknown tool {tool_name}")
    try:
        return handlers[tool_name](arguments, ctx)
    except ScreenError as error:
        return {"ok": False, "error": str(error)}


def _launch(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    command = [str(part) for part in args["command"]]
    gl = bool(args.get("gl"))
    verdict = ctx.display.authorize(command, str(args.get("reason") or ""))
    if not verdict.get("allowed"):
        return {"ok": False, "error": f"not started: {verdict.get('reason') or 'the person did not allow it'}"}
    if gl and not SCREEN.glx:
        if SCREEN.running_apps():
            raise ScreenError("this program needs OpenGL, which the screen has to be restarted for; "
                              "close the running programs first (apps lists them)")
        import kvist_display

        kvist_display.restart_display(glx=True)
        SCREEN.reconnect()
        SCREEN.glx = True
    started = SCREEN.launch(command, gl=gl)
    return {"ok": True, "authorized_by": verdict.get("by"), **started}


def _may_analyze(ctx: Any) -> bool:
    granted = ctx.permissions.current()
    if "images.analyze" in granted["granted"] or "images.analyze" in granted["for_this_task"]:
        return True
    return bool(ctx.permissions.request("images.analyze", "describe the screen with the Vision model").get("granted"))


def _screenshot(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    shot = ctx.display.screenshot(f"screen-{int(time.time())}.png", cursor=SCREEN.cursor_image())
    result: dict[str, Any] = {"ok": True, "attachment_id": shot["attachment_id"], "width": shot["width"],
                              "height": shot["height"], "pointer": list(SCREEN.pointer())}
    question = args.get("question")
    if question and not _may_analyze(ctx):
        result["description_error"] = "the person did not allow describing the screen with the Vision model"
    elif question:
        try:
            result["description"] = ctx.images.analyze([shot["attachment_id"]], question)["text"]
        except Exception as error:  # noqa: BLE001 - Vision being unavailable is reported, the screenshot stays
            result["description_error"] = str(error)
    return result


def _mouse(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    action = args["action"]
    slow = bool(args.get("slow"))
    button = BUTTONS[args.get("button", "left")]
    if action in ("move", "click", "double_click", "press", "drag") and (args.get("x") is None) != (args.get("y") is None):
        raise ScreenError("give both x and y, or neither to stay where the pointer is")
    if args.get("x") is not None:
        SCREEN.move(int(args["x"]), int(args["y"]), slow=slow)
    if action == "click":
        SCREEN.click(button)
    elif action == "double_click":
        SCREEN.click(button, 2)
    elif action == "press":
        SCREEN.button(button, True)
    elif action == "release":
        SCREEN.button(button, False)
    elif action == "drag":
        if args.get("to_x") is None or args.get("to_y") is None:
            raise ScreenError("drag needs to_x and to_y")
        time.sleep(motion.settle_pause(_RNG))
        SCREEN.button(button, True)
        try:
            time.sleep(motion.click_hold(_RNG))
            SCREEN.move(int(args["to_x"]), int(args["to_y"]), slow=True)
            time.sleep(motion.settle_pause(_RNG))
        finally:
            SCREEN.button(button, False)
    elif action == "scroll":
        SCREEN.scroll(int(args.get("dx") or 0), int(args.get("dy") or 0))
    elif action != "move":
        raise ScreenError(f"unknown action {action!r}")
    return {"ok": True, "pointer": list(SCREEN.pointer())}


def _keyboard(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if args.get("text") is not None:
        text = str(args["text"])
        if len(text) > MAX_TEXT:
            raise ScreenError(f"text is longer than {MAX_TEXT} characters; paste it in pieces")
        if args.get("paste"):
            SCREEN.paste(text, str(args.get("paste_keys") or "ctrl+v"))
        else:
            SCREEN.type_text(text)
    if args.get("keys"):
        SCREEN.press_combo(str(args["keys"]))
    return {"ok": True}


def _windows(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    op = args.get("op", "list")
    window_id = args.get("id")
    if op != "list" and window_id is None:
        raise ScreenError(f"{op} needs a window id; call windows with op list")
    if op == "focus":
        SCREEN.focus(int(window_id))
    elif op == "move":
        SCREEN.place(int(window_id), args.get("x"), args.get("y"), None, None)
    elif op == "resize":
        SCREEN.place(int(window_id), None, None, args.get("width"), args.get("height"))
    elif op == "minimize":
        SCREEN.minimize(int(window_id))
    elif op == "close":
        SCREEN.close(int(window_id))
    elif op != "list":
        raise ScreenError(f"unknown op {op!r}")
    time.sleep(0.2 if op != "list" else 0)
    return {"ok": True, "windows": SCREEN.windows()[:50]}


def _apps(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if args.get("stop_all"):
        return {"ok": True, "stopped": SCREEN.end_apps()}
    return {"ok": True, "apps": SCREEN.running_apps()}


def _pss_mib(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as handle:
            return round(sum(int(line.split()[1]) for line in handle if line.startswith("Pss:")) / 1024, 1)
    except OSError:
        return None


def _status(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Proportional memory of the screen's own parts (programs started here are not counted), against the budget."""
    import kvist_display

    parts = {name: _pss_mib(pid) for name, pid in kvist_display.display_processes().items()}
    parts["skill_process"] = _pss_mib(os.getpid())
    total = round(sum(value for value in parts.values() if value), 1)
    width, height = SCREEN.size()
    return {"ok": True, "pss_mib": parts, "total_mib": total, "budget_mib": BUDGET_MIB,
            "within_budget": total <= BUDGET_MIB, "screen": f"{width}x{height}", "gl": SCREEN.glx,
            "pointer": list(SCREEN.pointer())}
