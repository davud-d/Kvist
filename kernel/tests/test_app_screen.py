# -*- coding: utf-8 -*-
"""The app_screen skill: pointer paths (pure) and the plugin against a real Xvfb with its own event-recording client."""
from __future__ import annotations

import base64
import io
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills_bundled" / "app_screen"
RUNNER_DIR = Path(__file__).resolve().parents[1] / "plugins" / "runner"


@pytest.fixture(scope="module")
def motion():
    sys.path.insert(0, str(SKILL_DIR))
    import motion as module

    yield module
    sys.path.remove(str(SKILL_DIR))


def test_a_pointer_path_ends_on_the_exact_target_and_is_never_the_same_twice(motion):
    paths = [motion.pointer_path((100, 100), (1500, 800), random.Random(seed)) for seed in range(20)]

    assert all(path[-1][:2] == (1500, 800) for path in paths)
    assert len({tuple(point[:2] for point in path) for path in paths}) == 20
    assert len({len(path) for path in paths}) > 5


def test_a_pointer_path_curves_starts_slowly_and_has_a_human_duration(motion):
    path = motion.pointer_path((0, 0), (1000, 0), random.Random(7))
    straight = max(abs(y) for _, y, _ in path)
    first_step = math.hypot(path[0][0], path[0][1])
    steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]

    assert straight >= 3                      # it bends away from the straight line
    assert first_step < max(steps) / 3        # eased start, not a jump
    assert 0.15 <= sum(delay for *_, delay in path) <= 1.5
    assert len({round(delay, 4) for *_, delay in path}) > len(path) * 0.8  # not a constant tick


def test_no_move_when_already_there(motion):
    assert motion.pointer_path((5, 5), (5, 5), random.Random(1)) == [(5, 5, 0.0)]


# --- a real X server --------------------------------------------------------

needs_x = pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb is not installed")


def _xlib():
    """python-xlib is a pure-Python system package (the skill's runner uses the system Python); the project's
    venv may be another minor version without it, so the system copy is added to the path when needed."""
    try:
        import Xlib  # noqa: F401
    except ImportError:
        import glob

        found = glob.glob("/usr/lib/python3*/site-packages/Xlib")
        if found:
            sys.path.append(str(Path(found[0]).parent))
    return pytest.importorskip("Xlib", reason="python-xlib is not installed")


@pytest.fixture(scope="module")
def screen(tmp_path_factory):
    _xlib()
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb is not installed")
    fb_dir = tmp_path_factory.mktemp("fb")
    read_end, write_end = os.pipe()
    server = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_end), "-screen", "0", "1920x1080x24", "-nolisten", "tcp", "-nocursor",
         "-extension", "GLX", "-fbdir", str(fb_dir)],
        pass_fds=(write_end,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_end)
    number = os.read(read_end, 16).decode().strip()
    os.close(read_end)
    os.environ["DISPLAY"] = f":{number}"
    sys.path[:0] = [str(SKILL_DIR), str(RUNNER_DIR)]
    import plugin

    plugin.SCREEN.dpy = None
    yield plugin, fb_dir / "Xvfb_screen0"
    plugin.SCREEN.end_apps()
    server.terminate()
    server.wait(timeout=5)
    for path in (str(SKILL_DIR), str(RUNNER_DIR)):
        sys.path.remove(path)


class Recorder:
    """A client with one window that records the events it receives, as a program would see them."""

    def __init__(self) -> None:
        from Xlib import X, display

        self.d = display.Display()
        root = self.d.screen().root
        self.window = root.create_window(100, 100, 600, 400, 0, self.d.screen().root_depth,
                                         event_mask=(X.ButtonPressMask | X.ButtonReleaseMask | X.PointerMotionMask
                                                     | X.KeyPressMask | X.KeyReleaseMask))
        self.window.set_wm_name("recorder")
        self.window.map()
        self.d.sync()
        self.events: list[tuple] = []
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        from Xlib import X

        while not self._stop:
            if not self.d.pending_events():
                time.sleep(0.002)
                continue
            event = self.d.next_event()
            now = time.monotonic()
            if event.type == X.MappingNotify:
                self.d.refresh_keyboard_mapping(event)
            if event.type in (X.ButtonPress, X.ButtonRelease):
                self.events.append(("button", event.type == X.ButtonPress, event.detail,
                                    event.root_x, event.root_y, now))
            elif event.type == X.MotionNotify:
                self.events.append(("motion", event.root_x, event.root_y, now))
            elif event.type == X.KeyPress:
                self.events.append(("key", self.d.keycode_to_keysym(event.detail, 0),
                                    self.d.keycode_to_keysym(event.detail, 1), now))

    def stop(self) -> None:
        self._stop = True
        self._thread.join(timeout=2)
        self.window.destroy()
        self.d.sync()
        self.d.close()


@pytest.fixture
def recorder(screen):
    plugin, _ = screen
    recording = Recorder()
    time.sleep(0.2)
    plugin.SCREEN.move(300, 300)  # inside the recorder's window
    recording.events.clear()
    yield recording
    recording.stop()


@needs_x
def test_the_pointer_arrives_on_the_exact_pixel_by_many_small_steps(screen, recorder):
    plugin, _ = screen

    plugin._mouse({"action": "move", "x": 611, "y": 437}, None)
    time.sleep(0.1)
    motions = [event for event in recorder.events if event[0] == "motion"]

    assert plugin.SCREEN.pointer() == (611, 437)
    assert len(motions) > 5
    assert motions[-1][1:3] == (611, 437)


@needs_x
def test_click_double_click_right_click_and_scroll_reach_the_program_as_button_events(screen, recorder):
    plugin, _ = screen
    plugin._mouse({"action": "click", "x": 320, "y": 320}, None)
    plugin._mouse({"action": "double_click"}, None)
    plugin._mouse({"action": "click", "button": "right"}, None)
    plugin._mouse({"action": "scroll", "dy": 3}, None)
    plugin._mouse({"action": "scroll", "dy": -2}, None)
    time.sleep(0.2)

    buttons = [(event[2], event[1]) for event in recorder.events if event[0] == "button"]

    assert buttons[:6] == [(1, True), (1, False), (1, True), (1, False), (1, True), (1, False)]
    assert buttons[6:8] == [(3, True), (3, False)]
    assert [number for number, pressed in buttons[8:] if pressed] == [5, 5, 5, 4, 4]
    presses = [event for event in recorder.events if event[0] == "button" and event[1] and event[2] == 1]
    assert presses[2][5] - presses[1][5] < 0.4   # the double click is two clicks close together


@needs_x
def test_a_drag_holds_the_button_across_the_move_and_releases_on_the_exact_target(screen, recorder):
    plugin, _ = screen

    plugin._mouse({"action": "drag", "x": 200, "y": 200, "to_x": 500, "to_y": 350}, None)
    time.sleep(0.2)

    kinds = [event[0] if event[0] == "motion" else ("down" if event[1] else "up") for event in recorder.events
             if event[0] in ("motion", "button")]
    release = [event for event in recorder.events if event[0] == "button" and not event[1]][-1]
    assert kinds.index("down") < kinds.index("up")
    assert "motion" in kinds[kinds.index("down"):kinds.index("up")]
    assert release[3:5] == (500, 350)


@needs_x
def test_unicode_text_and_key_combinations_are_typed_like_a_keyboard(screen, recorder):
    plugin, _ = screen

    plugin._keyboard({"text": "aZ Привет ✓"}, None)
    plugin._keyboard({"keys": "ctrl+shift+v"}, None)
    time.sleep(0.3)

    from Xlib import XK

    typed = [event for event in recorder.events if event[0] == "key"]
    ordinary = [event[1] for event in typed[:4]]

    seen = {event[1] for event in typed}

    assert ordinary[0] == XK.string_to_keysym("a")
    assert {0x01000000 + ord("П"), 0x01000000 + ord("✓")} <= seen  # characters the layout lacks arrive as themselves
    assert len(typed) >= 11 + 3 + 1  # the characters, ctrl+shift, and v


@needs_x
def test_the_screenshot_carries_the_real_cursor_where_the_pointer_is(screen, recorder):
    plugin, fb_path = screen
    from PIL import Image

    from kernel.plugins.screen import frame_png

    plugin.SCREEN.move(900, 700)
    cursor = plugin.SCREEN.cursor_image()
    without = Image.open(io.BytesIO(frame_png(fb_path))).convert("RGB")
    with_cursor = Image.open(io.BytesIO(frame_png(fb_path, cursor=cursor))).convert("RGB")
    box = (900 - 20, 700 - 20, 900 + 20, 700 + 20)

    assert cursor["width"] > 0 and base64.b64decode(cursor["pixels"])
    assert with_cursor.crop(box).tobytes() != without.crop(box).tobytes()
    assert with_cursor.crop((0, 0, 100, 100)).tobytes() == without.crop((0, 0, 100, 100)).tobytes()


@needs_x
def test_windows_can_be_listed_moved_resized_and_focused_without_a_window_manager(screen, recorder):
    plugin, _ = screen

    listed = plugin._windows({"op": "list"}, None)["windows"]
    mine = next(window for window in listed if window["title"] == "recorder")
    plugin._windows({"op": "move", "id": mine["id"], "x": 40, "y": 60}, None)
    plugin._windows({"op": "resize", "id": mine["id"], "width": 500, "height": 300}, None)
    moved = next(window for window in plugin._windows({"op": "focus", "id": mine["id"]}, None)["windows"]
                 if window["id"] == mine["id"])

    assert (moved["x"], moved["y"], moved["width"], moved["height"]) == (40, 60, 500, 300)
    assert plugin.execute("windows", {"op": "close", "id": 12345}, None)["ok"] is False


@needs_x
def test_a_program_that_is_not_allowed_never_starts(screen):
    plugin, _ = screen

    class Refusing:
        class display:
            @staticmethod
            def authorize(command, reason=""):
                return {"allowed": False, "reason": "the person did not allow it"}

    result = plugin.execute("launch", {"command": ["xterm"]}, Refusing)

    assert result["ok"] is False and "not started" in result["error"]
    assert plugin.SCREEN.running_apps() == []


@needs_x
def test_status_reports_memory_against_the_forty_mib_budget(screen):
    plugin, _ = screen

    status = plugin._status({}, None)

    assert status["budget_mib"] == 40.0
    assert status["screen"] == "1920x1080"
    assert status["pss_mib"]["skill_process"] > 0


# --- the whole skill through the real runner (Bubblewrap, its own Xvfb) ------

def _shipped_with_available_wm(target: Path) -> Path:
    """The shipped package; JWM may not be installed on a build machine, then i3 stands in for this run only."""
    import json

    shutil.copytree(SKILL_DIR, target, ignore=shutil.ignore_patterns("__pycache__"))
    if shutil.which("jwm") is None:
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        manifest["display"]["wm"] = "i3"
        manifest["dependencies"]["system"] = ["Xvfb", "i3", "xclip"]
        (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return target


def test_the_skill_runs_in_the_sandbox_and_the_project_rules_decide_what_may_start(agent_factory, tmp_path, monkeypatch):
    from kernel.plugins.registry import SkillRegistry
    from kernel.plugins.sandbox import runner_unavailable_reason
    from kernel.tests.skill_fixtures import install
    from kernel.tests.test_skill_runtime import _agent, _call, _feedback, _script

    if shutil.which("Xvfb") is None or shutil.which("xterm") is None:
        pytest.skip("needs Xvfb and xterm")
    if runner_unavailable_reason():
        pytest.skip(runner_unavailable_reason())
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config))
    registry = SkillRegistry(config / "plugins" / "skill")
    install(registry, _shipped_with_available_wm(tmp_path / "pkg"))
    agent, ui = _agent(agent_factory)
    rules = Path(agent.executor.workspace) / ".kvist" / "permissions.json"
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text('{"allow": ["xterm"], "deny": ["badapp *"]}', encoding="utf-8")
    _script(agent, [
        _call("skills", {"op": "attach", "skill": "app_screen"}),
        _call("app_screen__status", {}),
        _call("app_screen__launch", {"command": ["xterm"], "reason": "look at it"}),
        _call("app_screen__mouse", {"action": "click", "x": 400, "y": 300}),
        _call("app_screen__keyboard", {"text": "echo hi\n"}),
        _call("app_screen__screenshot", {}),
        _call("app_screen__launch", {"command": ["badapp", "--now"]}),
        "Done.",
    ])

    agent._run_loop("check the screen", start_step=1)

    results = [log["feedback"]["data"]["result"] for log in _feedback(agent, "skill_call")]
    assert len(results) == 6, [log.get("feedback") for log in agent._state.step_logs]
    status, launched, clicked, typed, shot, denied = results
    assert status["screen"] == "1920x1080"
    assert status["pss_mib"]["server"] > 0 and status["pss_mib"]["wm"] > 0
    assert (status["cursor"]["width"], status["cursor"]["height"]) == (56, 56)  # the window manager's own pointer is the theme's
    assert launched["ok"] is True, launched
    assert launched["authorized_by"] == "rule" and launched["windows"]
    assert clicked["pointer"] == [400, 300] and typed["ok"] is True
    assert shot["ok"] is True and (shot["width"], shot["height"]) == (1920, 1080) and shot["pointer"] == [400, 300]
    assert denied["ok"] is False and "permissions.json" in denied["error"]


def test_a_gl_terminal_needs_the_screen_restarted_with_glx_and_then_runs(agent_factory, tmp_path, monkeypatch):
    from kernel.plugins.registry import SkillRegistry
    from kernel.plugins.sandbox import runner_unavailable_reason
    from kernel.tests.skill_fixtures import install
    from kernel.tests.test_skill_runtime import _agent, _call, _feedback, _script

    if shutil.which("Xvfb") is None or shutil.which("kitty") is None:
        pytest.skip("needs Xvfb and kitty")
    if runner_unavailable_reason():
        pytest.skip(runner_unavailable_reason())
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config))
    registry = SkillRegistry(config / "plugins" / "skill")
    install(registry, _shipped_with_available_wm(tmp_path / "pkg"))
    agent, ui = _agent(agent_factory)
    rules = Path(agent.executor.workspace) / ".kvist" / "permissions.json"
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text('{"allow": ["kitty *"]}', encoding="utf-8")
    _script(agent, [
        _call("skills", {"op": "attach", "skill": "app_screen"}),
        _call("app_screen__status", {}),
        _call("app_screen__launch", {"command": ["kitty", "-o", "confirm_os_window_close=0"], "gl": True}),
        _call("app_screen__keyboard", {"text": "echo Привет из kitty\n"}),
        _call("app_screen__mouse", {"action": "move", "x": 700, "y": 500}),
        _call("app_screen__screenshot", {}),
        _call("app_screen__status", {}),
        "Done.",
    ])

    agent._run_loop("start a terminal", start_step=1)

    failed = [log["feedback"] for log in _feedback(agent, "skill_call") if not log["feedback"].get("ok")]
    assert not failed, [str(item)[:900] for item in failed]
    results = [log["feedback"]["data"]["result"] for log in _feedback(agent, "skill_call")]
    assert len(results) == 6
    before, launched, _typed, _moved, shot, after = results
    assert shot["ok"] is True and shot["pointer"] == [700, 500]
    assert before["gl"] is False and launched["ok"] is True, launched
    assert launched["windows"] and after["gl"] is True
    print("PSS before:", before["pss_mib"], before["total_mib"], "after GLX:", after["pss_mib"], after["total_mib"])
    assert after["pss_mib"]["server"] > before["pss_mib"]["server"] + 8  # the software renderer is the cost; Mesa builds differ (+13 MiB measured Nov 2026, +26 before)


# --- the pointer on the core's live preview ---------------------------------

@needs_x
def test_the_pointer_is_published_for_the_live_preview_and_drawn_by_the_viewer(screen, tmp_path, monkeypatch):
    plugin, fb_path = screen
    from kernel.plugins.screen import CURSOR_FILE, frame_jpeg, read_cursor

    monkeypatch.setattr(plugin, "CURSOR_FILE", str(tmp_path / CURSOR_FILE))
    plugin._mouse({"action": "move", "x": 1000, "y": 600}, None)
    plugin.SCREEN.refresh_cursor()
    published = read_cursor(tmp_path / CURSOR_FILE)

    assert published is not None and (published["x"], published["y"]) == (1000, 600)
    with_pointer = frame_jpeg(fb_path, cursor=published)
    without = frame_jpeg(fb_path)
    assert with_pointer != without


def test_an_oversized_or_broken_cursor_file_is_ignored(tmp_path):
    from kernel.plugins.screen import MAX_CURSOR_BYTES, frame_jpeg, read_cursor

    big, broken, odd = tmp_path / "big.json", tmp_path / "broken.json", tmp_path / "odd.json"
    big.write_text('{"x": 1, "pad": "' + "a" * MAX_CURSOR_BYTES + '"}')
    broken.write_text("{not json")
    odd.write_text("[1, 2]")

    assert read_cursor(big) is None and read_cursor(broken) is None and read_cursor(odd) is None
    assert read_cursor(tmp_path / "missing.json") is None
    assert callable(frame_jpeg)


@needs_x
def test_programs_on_the_screen_really_use_the_skills_own_blue_pointer(screen, monkeypatch):
    """The cursor is the X server's own: a program that sets its text cursor gets the themed I-beam, and the
    XFixes image any viewer reads back is that blue-glow picture, not a drawing laid over the preview."""
    plugin, _ = screen
    if shutil.which("xterm") is None:
        pytest.skip("needs xterm")
    theme = SKILL_DIR / "icons"
    monkeypatch.setenv("XCURSOR_PATH", str(theme))
    monkeypatch.setenv("XCURSOR_THEME", "kvist")
    monkeypatch.setenv("XCURSOR_SIZE", "32")
    started = plugin.SCREEN.launch(["xterm"], gl=False)
    window = started["windows"][0]
    plugin.SCREEN.move(window["x"] + 100, window["y"] + 100)   # inside the text area: xterm asks for its I-beam
    time.sleep(0.3)

    image = plugin.SCREEN.cursor_image()
    pixels = base64.b64decode(image["pixels"])
    bluish = sum(1 for index in range(0, len(pixels), 4)
                 if pixels[index] > pixels[index + 2] + 40 and pixels[index + 3] > 40)  # BGRA: blue well above red

    assert (image["width"], image["height"]) == (56, 56)    # the theme's canvas, not the server's 16x16 default
    assert bluish > 100
    plugin.SCREEN.end_apps()


# --- the live preview: native size, high quality, 60 fps while something moves, quiet while nothing does ----

def _frames_in(viewer, seconds: float) -> list[bytes]:
    """The stream parts received in ``seconds`` (a multipart MJPEG read by hand, like the interfaces do)."""
    import socket

    sock = socket.create_connection(("127.0.0.1", viewer.port), timeout=3)
    sock.sendall(f"GET /stream.mjpeg?t={viewer.token} HTTP/1.0\r\nHost: 127.0.0.1:{viewer.port}\r\n\r\n".encode())
    reader = sock.makefile("rb")
    while reader.readline().strip():
        pass
    frames, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        length = 0
        while True:
            line = reader.readline().strip()
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
            if not line and length:
                break
        frames.append(reader.read(length))
        reader.readline()
    sock.close()
    return frames


@needs_x
def test_the_live_preview_is_full_size_sharp_fast_while_the_pointer_moves_and_quiet_when_nothing_changes(screen, tmp_path):
    plugin, fb_path = screen
    from kernel.plugins.screen import CURSOR_FILE, ScreenViewer

    viewer = ScreenViewer(fb_path.parent, title="t")
    viewer.start()
    try:
        still = _frames_in(viewer, 2.6)
        stop = threading.Event()

        def move() -> None:   # the skill publishing a pointer that keeps moving
            cursor = plugin.SCREEN.cursor_image()
            step = 0
            while not stop.is_set():
                step += 1
                (fb_path.parent / CURSOR_FILE).write_text(__import__("json").dumps({**cursor, "x": 100 + step % 900, "y": 300}))
                time.sleep(0.004)

        mover = threading.Thread(target=move, daemon=True)
        mover.start()
        moving = _frames_in(viewer, 1.5)
        stop.set()
        mover.join(timeout=2)
    finally:
        viewer.close()
        (fb_path.parent / CURSOR_FILE).unlink(missing_ok=True)

    from PIL import Image

    print(f"live preview: still {len(still) / 2.6:.1f} fps, moving {len(moving) / 1.5:.1f} fps, "
          f"{len(moving[-1]) // 1024} KiB a frame")
    assert len(still) <= 8                      # a still screen is not streamed at 60: a few repeats keep the read alive
    assert len(moving) >= 1.5 * 40              # at least 40 frames a second while the pointer moves (60 asked)
    assert Image.open(io.BytesIO(moving[-1])).size == (1920, 1080)   # native size, not scaled down
