"""Actions on the person's own desktop: what may be opened, how an application is found, who confirms a close."""
from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from kernel.plugins import host_actions
from kernel.plugins.host import ServiceError
from kernel.plugins.services import MAX_HOST_OPENS_PER_RUN, CoreServices


def _desktop_entry(directory, name, title, extra=""):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(f"[Desktop Entry]\nType=Application\nName={title}\nExec=true\n{extra}", encoding="utf-8")


xdg_data_dirs = pytest.mark.skipif(
    os.name == "nt",
    reason="XDG_DATA_DIRS is colon-separated (the XDG spec), so it cannot name a Windows temporary folder (C:\\...); "
    "desktop entries are looked up on Linux only",
)


@pytest.fixture
def apps_home(tmp_path, monkeypatch):
    monkeypatch.setattr(host_actions.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    return tmp_path


# --- addresses ---------------------------------------------------------------


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "https://user:pw@example.com/",
                                 "https:///nohost", "example.com", "http://[::1"])
def test_only_plain_web_addresses_are_opened(monkeypatch, url):
    opened = []
    monkeypatch.setattr(host_actions.webbrowser, "open", lambda *args, **kwargs: opened.append(args) or True)

    with pytest.raises(ServiceError) as error:
        host_actions.open_url(url)

    assert error.value.code == "bad_request" and opened == []


def test_an_address_opens_in_a_new_tab(monkeypatch):
    seen = {}
    monkeypatch.setattr(host_actions.webbrowser, "open", lambda url, new: seen.update(url=url, new=new) or True)

    host_actions.open_url("https://www.youtube.com/watch?v=abc")

    assert seen == {"url": "https://www.youtube.com/watch?v=abc", "new": 2}


def test_a_missing_browser_is_unavailable_not_success(monkeypatch):
    monkeypatch.setattr(host_actions.webbrowser, "open", lambda *args, **kwargs: False)

    with pytest.raises(ServiceError) as error:
        host_actions.open_url("https://example.com/")

    assert error.value.code == "unavailable"


# --- applications ------------------------------------------------------------


@xdg_data_dirs
def test_applications_are_found_by_id_or_name_and_hidden_ones_are_not_offered(apps_home):
    _desktop_entry(apps_home / "system" / "applications", "org.telegram.desktop.desktop", "Telegram Desktop")
    _desktop_entry(apps_home / "system" / "applications", "secret.desktop", "Secret", "NoDisplay=true\n")
    _desktop_entry(apps_home / "home" / "applications", "org.telegram.desktop.desktop", "Telegram (mine)")

    apps = host_actions.installed_apps()

    assert apps == {"org.telegram.desktop": "Telegram (mine)"}
    assert host_actions.resolve_app("telegram", apps) == ("org.telegram.desktop", "Telegram (mine)")


def test_an_ambiguous_or_unknown_name_is_answered_with_the_candidates(apps_home):
    apps = {"firefox": "Firefox", "firefox-developer": "Firefox Developer Edition"}

    with pytest.raises(ServiceError) as ambiguous:
        host_actions.resolve_app("fire", apps)
    with pytest.raises(ServiceError) as unknown:
        host_actions.resolve_app("emacs", apps)

    assert ambiguous.value.code == "ambiguous" and "Firefox Developer Edition [firefox-developer]" in ambiguous.value.message
    assert unknown.value.code == "not_found"
    assert host_actions.resolve_app("firefox", apps)[0] == "firefox"


@xdg_data_dirs
def test_an_application_is_started_through_its_desktop_entry(apps_home, monkeypatch):
    _desktop_entry(apps_home / "system" / "applications", "firefox.desktop", "Firefox")
    ran = []
    monkeypatch.setattr(host_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host_actions.subprocess, "run",
                        lambda command, **kwargs: ran.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    assert host_actions.open_app("fire") == "Firefox"
    assert ran == [["/usr/bin/gtk-launch", "firefox.desktop"]]


@xdg_data_dirs
def test_an_id_that_itself_ends_in_desktop_is_launched_with_the_full_file_id(apps_home, monkeypatch):
    _desktop_entry(apps_home / "system" / "applications", "org.telegram.desktop.desktop", "Telegram")
    ran = []
    monkeypatch.setattr(host_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host_actions.subprocess, "run",
                        lambda command, **kwargs: ran.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    host_actions.open_app("telegram")

    assert ran == [["/usr/bin/gtk-launch", "org.telegram.desktop.desktop"]]


def test_entries_that_share_a_name_are_never_guessed_and_are_chosen_by_an_explicit_id():
    apps = {"kvist": "Kvist", "Kvist": "Kvist"}

    with pytest.raises(ServiceError) as ambiguous:
        host_actions.resolve_app("Kvist", apps)

    assert ambiguous.value.code == "ambiguous"
    assert "Kvist [Kvist]" in ambiguous.value.message and "Kvist [kvist]" in ambiguous.value.message
    assert 'id:' in ambiguous.value.message
    assert host_actions.resolve_app("id:kvist", apps) == ("kvist", "Kvist")
    assert host_actions.resolve_app("id:Kvist", apps) == ("Kvist", "Kvist")
    with pytest.raises(ServiceError) as unknown:
        host_actions.resolve_app("id:nothing", apps)
    assert unknown.value.code == "not_found"


# --- windows -----------------------------------------------------------------


class Commands:
    """Stands in for the system commands: answers by the first words of the command line."""

    def __init__(self, monkeypatch, answers):
        self.sent, self.answers = [], answers
        monkeypatch.setattr(host_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(host_actions.subprocess, "run", self.run)

    def run(self, command, **kwargs):
        self.sent.append(command[1:] if command[0].startswith("/") else command)
        key = next(prefix for prefix in self.answers if self.sent[-1][:len(prefix)] == list(prefix))
        return SimpleNamespace(returncode=0, stdout=self.answers[key], stderr="")

    def acted(self):
        return [command for command in self.sent if "dispatch" in command]


@pytest.fixture
def linux(monkeypatch):
    for name in ("HYPRLAND_INSTANCE_SIGNATURE", "I3SOCK", "SWAYSOCK", "NIRI_SOCKET", "DISPLAY",
                 "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(host_actions.sys, "platform", "linux")


HYPR = json.dumps([
    {"address": "0xaa", "class": "firefox", "title": "Rust video", "workspace": {"name": "1"}, "focusHistoryID": 0,
     "mapped": True},
    {"address": "0xbb", "class": "kitty", "title": "shell", "workspace": {"name": "2"}, "focusHistoryID": 1,
     "mapped": True},
    {"address": "0xcc", "class": "ghost", "title": "unmapped", "workspace": {"name": "2"}, "focusHistoryID": 2,
     "mapped": False},
])


def test_hyprland_windows_list_the_mapped_ones_and_dispatch_by_listed_id(linux, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    commands = Commands(monkeypatch, {("hyprctl", "clients"): HYPR, ("hyprctl", "dispatch"): "ok"})

    listed = host_actions.windows()
    host_actions.focus_window("0xbb")
    with pytest.raises(ServiceError) as gone:
        host_actions.close_window("0xdead")

    assert listed[0] == {"id": "0xaa", "app": "firefox", "title": "Rust video", "workspace": "1", "focused": True}
    assert [item["id"] for item in listed] == ["0xaa", "0xbb"]
    assert commands.acted() == [["hyprctl", "dispatch", "focuswindow", "address:0xbb"]]
    assert gone.value.code == "gone"


I3_TREE = json.dumps({"type": "root", "nodes": [{"type": "output", "nodes": [
    {"type": "workspace", "name": "2:web", "nodes": [
        {"type": "con", "window": 100, "name": "Rust video - Waterfox", "focused": True,
         "window_properties": {"class": "Waterfox"}, "nodes": []}],
     "floating_nodes": [{"type": "floating_con", "nodes": [
         {"type": "con", "window": 200, "name": "Telegram", "focused": False,
          "window_properties": {"class": "TelegramDesktop"}, "nodes": []}]}]}]}]})


def test_i3_windows_come_from_the_tree_and_close_asks_politely(linux, monkeypatch):
    monkeypatch.setenv("I3SOCK", "/run/user/1000/i3/ipc-socket")
    commands = Commands(monkeypatch, {("i3-msg", "-t"): I3_TREE, ("i3-msg", "[id=200] kill"): '[{"success": true}]',
                                      ("i3-msg", "[id=100] focus"): '[{"success": false, "error": "No such window"}]'})

    listed = host_actions.windows()
    host_actions.close_window("200")
    with pytest.raises(ServiceError, match="No such window"):
        host_actions.focus_window("100")

    assert listed == [
        {"id": "100", "app": "Waterfox", "title": "Rust video - Waterfox", "workspace": "2:web", "focused": True},
        {"id": "200", "app": "TelegramDesktop", "title": "Telegram", "workspace": "2:web", "focused": False}]
    assert ["i3-msg", "[id=200] kill"] in commands.sent


SWAY_TREE = json.dumps({"type": "root", "nodes": [{"type": "output", "nodes": [
    {"type": "workspace", "name": "1", "nodes": [
        {"type": "con", "id": 11, "pid": 900, "app_id": "firefox", "name": "Rust video", "focused": True, "nodes": []},
        {"type": "con", "id": 12, "pid": 901, "app_id": None, "name": "Telegram", "focused": False,
         "window_properties": {"class": "TelegramDesktop"}, "nodes": []},
        {"type": "con", "id": 13, "nodes": [], "name": "a split, not a window"}], "floating_nodes": []}]}]})


def test_sway_windows_use_container_ids_and_xwayland_windows_keep_their_class(linux, monkeypatch):
    monkeypatch.setenv("SWAYSOCK", "/run/user/1000/sway-ipc.sock")
    commands = Commands(monkeypatch, {("swaymsg", "-t"): SWAY_TREE, ("swaymsg", "[con_id=12] kill"): '[{"success": true}]'})

    listed = host_actions.windows()
    host_actions.close_window("12")

    assert listed == [
        {"id": "11", "app": "firefox", "title": "Rust video", "workspace": "1", "focused": True},
        {"id": "12", "app": "TelegramDesktop", "title": "Telegram", "workspace": "1", "focused": False}]
    assert ["swaymsg", "[con_id=12] kill"] in commands.sent


def test_niri_windows_are_listed_as_json_and_acted_on_by_id(linux, monkeypatch):
    monkeypatch.setenv("NIRI_SOCKET", "/run/user/1000/niri.sock")
    rows = [{"id": 7, "title": "Rust video", "app_id": "firefox", "workspace_id": 2, "is_focused": True},
            {"id": 8, "title": None, "app_id": None, "workspace_id": 2, "is_focused": False}]
    commands = Commands(monkeypatch, {("niri", "msg", "--json"): json.dumps(rows), ("niri", "msg", "action"): ""})

    listed = host_actions.windows()
    host_actions.focus_window("8")

    assert listed[0] == {"id": "7", "app": "firefox", "title": "Rust video", "workspace": "2", "focused": True}
    assert listed[1]["title"] == "" and listed[1]["app"] == ""
    assert ["niri", "msg", "action", "focus-window", "--id", "8"] in commands.sent


def _gnome(monkeypatch, answers):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    return Commands(monkeypatch, answers)


def test_gnome_windows_come_from_the_window_calls_extension(linux, monkeypatch):
    listing = json.dumps([{"id": 2205525109, "wm_class": "firefox", "workspace": 0}])
    commands = _gnome(monkeypatch, {
        ("gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path",
         "/org/gnome/Shell/Extensions/Windows", "--method", "org.gnome.Shell.Extensions.Windows.List"): f"({listing!r},)\n",
        ("gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path",
         "/org/gnome/Shell/Extensions/Windows", "--method", "org.gnome.Shell.Extensions.Windows.Details"):
            "('{\"focus\": true}',)\n",
        ("gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path",
         "/org/gnome/Shell/Extensions/Windows", "--method", "org.gnome.Shell.Extensions.Windows.GetTitle"):
            "('It\\'s a title',)\n",
        ("gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path",
         "/org/gnome/Shell/Extensions/Windows", "--method", "org.gnome.Shell.Extensions.Windows.Close"): "()\n"})

    listed = host_actions.windows()
    host_actions.close_window("2205525109")

    assert listed == [{"id": "2205525109", "app": "firefox", "title": "It's a title", "workspace": "0",
                       "focused": True}]
    assert commands.sent[-1][-2:] == ["org.gnome.Shell.Extensions.Windows.Close", "2205525109"]


def test_gnome_without_the_extension_says_what_to_install(linux, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(host_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host_actions.subprocess, "run", lambda command, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="Error: GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: no such method"))

    with pytest.raises(ServiceError) as error:
        host_actions.windows()

    assert error.value.code == "unavailable" and "Window Calls" in error.value.message


def test_kde_windows_are_read_and_closed_with_kdotool(linux, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    first, second = "{11111111-2222-3333-4444-555555555555}", "{66666666-7777-8888-9999-000000000000}"
    names = {first: "Rust video", second: "Telegram"}

    def run(command, **kwargs):
        action = command[1]
        outputs = {"search": f"{first}\n{second}\n", "getactivewindow": second + "\n",
                   "getwindowclassname": "app\n", "getwindowname": names.get(command[-1], "") + "\n",
                   "windowclose": ""}
        sent.append(command[1:])
        return SimpleNamespace(returncode=0, stdout=outputs[action], stderr="")

    sent = []
    monkeypatch.setattr(host_actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(host_actions.subprocess, "run", run)

    listed = host_actions.windows()
    host_actions.close_window(first)

    assert [(item["title"], item["focused"]) for item in listed] == [("Rust video", False), ("Telegram", True)]
    assert ["windowclose", first] in sent


@pytest.mark.parametrize("desktop,session,needle", [
    ("KDE", "wayland", "kdotool"), ("GNOME", "wayland", "Window Calls"),
    ("river", "wayland", "no known window interface"), ("XFCE", "x11", "wmctrl")])
def test_a_desktop_without_a_window_interface_is_told_what_is_missing(linux, monkeypatch, desktop, session, needle):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    monkeypatch.setattr(host_actions.shutil, "which", lambda name: None)

    with pytest.raises(ServiceError) as error:
        host_actions.windows()

    assert error.value.code == "unavailable" and needle in error.value.message


def test_wmctrl_is_not_used_on_wayland_where_it_sees_only_xwayland(linux, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(host_actions.shutil, "which", lambda name: f"/usr/bin/{name}")

    with pytest.raises(ServiceError) as error:
        host_actions.windows()

    assert error.value.code == "unavailable"


def test_an_x11_window_manager_without_a_native_interface_is_driven_with_wmctrl(linux, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    commands = Commands(monkeypatch, {
        ("wmctrl", "-lx"): "0x04200003  0 Navigator.firefox host Rust video\n0x04400001  1 kitty.kitty host shell\n",
        ("xprop", "-root"): "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x4200003\n",
        ("wmctrl", "-i"): ""})

    listed = host_actions.windows()
    host_actions.close_window("0x04400001")

    assert listed[0] == {"id": "0x04200003", "app": "firefox", "title": "Rust video", "workspace": "0",
                         "focused": True}
    assert ["wmctrl", "-i", "-c", "0x04400001"] in commands.sent


def test_window_control_without_a_known_window_manager_is_unavailable(linux):
    with pytest.raises(ServiceError) as error:
        host_actions.windows()

    assert error.value.code == "unavailable" and "wmctrl" in error.value.message


def test_macos_windows_are_listed_and_closed_through_system_events(monkeypatch):
    monkeypatch.setattr(host_actions.sys, "platform", "darwin")
    row = {"id": "501:0", "app": "Safari", "title": "Rust video", "workspace": "", "focused": True}
    commands = Commands(monkeypatch, {("osascript", "-l", "JavaScript", "-e", host_actions._MAC_LIST): json.dumps([row]),
                                      ("osascript", "-l", "JavaScript", "-e", host_actions._MAC_ACT): ""})

    assert host_actions.windows() == [row]
    host_actions.close_window("501:0")

    assert commands.sent[-1][-3:] == ["501", "0", "close"]


def test_windows_windows_use_powershell_and_a_single_window_is_still_a_list(monkeypatch):
    monkeypatch.setattr(host_actions.sys, "platform", "win32")
    single = json.dumps({"Id": 4242, "ProcessName": "Telegram", "MainWindowTitle": "Telegram"})
    commands = Commands(monkeypatch, {("powershell", "-NoProfile", "-NonInteractive", "-Command", host_actions._WIN_LIST): single,
                                      ("powershell", "-NoProfile", "-NonInteractive", "-Command"): "4242\n"})

    listed = host_actions.windows()

    assert listed == [{"id": "4242", "app": "Telegram", "title": "Telegram", "workspace": "", "focused": True}]
    assert commands.sent[0][:3] == ["powershell", "-NoProfile", "-NonInteractive"]


def test_a_failing_command_reports_its_message(monkeypatch):
    monkeypatch.setattr(host_actions.subprocess, "run",
                        lambda command, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="no display"))

    with pytest.raises(ServiceError, match="no display"):
        host_actions._run(["/usr/bin/gtk-launch", "x"])


def test_a_missing_program_and_a_hanging_command_are_reported(monkeypatch):
    def missing(command, **kwargs):
        raise FileNotFoundError("no such file")

    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(host_actions.subprocess, "run", missing)
    with pytest.raises(ServiceError) as unavailable:
        host_actions._run(["/usr/bin/hyprctl", "clients"])
    monkeypatch.setattr(host_actions.subprocess, "run", hang)
    with pytest.raises(ServiceError) as timeout:
        host_actions._run(["/usr/bin/hyprctl", "clients"])

    assert unavailable.value.code == "unavailable" and timeout.value.code == "timeout"


# --- applications on other systems --------------------------------------------


def test_macos_apps_are_bundles_started_with_open(monkeypatch, tmp_path):
    (tmp_path / "system" / "Safari.app").mkdir(parents=True)
    (tmp_path / "home" / "Applications" / "Telegram.app").mkdir(parents=True)
    monkeypatch.setattr(host_actions, "_MAC_SYSTEM_APP_ROOTS", (tmp_path / "system",))
    monkeypatch.setattr(host_actions.sys, "platform", "darwin")
    monkeypatch.setattr(host_actions.Path, "home", lambda: tmp_path / "home")
    commands = Commands(monkeypatch, {("open", "-a"): ""})

    assert host_actions.installed_apps() == {"Safari": "Safari", "Telegram": "Telegram"}
    assert host_actions.open_app("telegram") == "Telegram"
    assert commands.sent == [["open", "-a", "Telegram"]]


def test_windows_apps_are_start_menu_shortcuts(monkeypatch, tmp_path):
    programs = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    (programs / "Telegram Desktop").mkdir(parents=True)
    (programs / "Telegram Desktop" / "Telegram.lnk").write_text("", encoding="utf-8")
    started = []
    monkeypatch.setattr(host_actions.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(host_actions.os, "startfile", started.append, raising=False)

    assert host_actions.installed_apps() == {"Telegram Desktop/Telegram": "Telegram"}
    assert host_actions.open_app("telegram") == "Telegram"
    assert started == [programs / "Telegram Desktop" / "Telegram.lnk"]


# --- the core service --------------------------------------------------------


class FakeUI:
    def __init__(self, answer=True):
        self.answer, self.asked, self.notes = answer, [], []

    def confirm(self, question, details=""):
        self.asked.append(question)
        return self.answer

    def update_live(self, **kwargs):
        self.notes.append(kwargs["user_message"])


def _services(ui, *, interactive=True):
    services = CoreServices.__new__(CoreServices)
    control = SimpleNamespace(awaiting_input=lambda _flag: None)
    services._agent = SimpleNamespace(
        _ui=ui, control=control, _state=SimpleNamespace(response_language="en", progress=0),
        config=SimpleNamespace(interactive=interactive))
    services._host_opens = {}
    return services


def _call(run_id="run"):
    return SimpleNamespace(run_id=run_id, skill=SimpleNamespace(manifest=SimpleNamespace(label=lambda _lang: "Desktop")))


def test_an_address_with_a_secret_inside_is_not_opened(monkeypatch):
    opened = []
    monkeypatch.setattr(host_actions, "open_url", opened.append)

    with pytest.raises(ServiceError) as error:
        _services(FakeUI())._host_open_url(_call(), {"url": "https://x.example/?k=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"})

    assert error.value.code == "denied" and opened == []


def test_opening_is_capped_per_run_and_counted_per_run(monkeypatch):
    monkeypatch.setattr(host_actions, "open_url", lambda url: None)
    services = _services(FakeUI())

    for index in range(MAX_HOST_OPENS_PER_RUN):
        services._host_open_url(_call(), {"url": f"https://example.com/{index}"})
    with pytest.raises(ServiceError) as error:
        services._host_open_url(_call(), {"url": "https://example.com/more"})
    services._host_open_url(_call("another run"), {"url": "https://example.com/fresh"})

    assert error.value.code == "denied"


def test_a_window_is_closed_only_after_the_person_agrees(monkeypatch):
    closed = []
    target = {"id": "0xaa", "app": "firefox", "title": "Rust video"}
    monkeypatch.setattr(host_actions, "window", lambda window_id: target)
    monkeypatch.setattr(host_actions, "close_window", closed.append)

    declined = _services(FakeUI(answer=False))._host_close(_call(), {"window": "0xaa"})
    accepted_ui = FakeUI(answer=True)
    accepted = _services(accepted_ui)._host_close(_call(), {"window": "0xaa"})

    assert declined["closed"] is False and accepted == {"closed": True} and closed == ["0xaa"]
    assert "firefox: Rust video" in accepted_ui.asked[0]


def test_nobody_to_ask_means_nothing_is_closed(monkeypatch):
    closed = []
    monkeypatch.setattr(host_actions, "window", lambda window_id: {"id": "0xaa", "app": "x", "title": "t"})
    monkeypatch.setattr(host_actions, "close_window", closed.append)

    with pytest.raises(ServiceError) as error:
        _services(FakeUI(), interactive=False)._host_close(_call(), {"window": "0xaa"})

    assert error.value.code == "denied" and closed == []
