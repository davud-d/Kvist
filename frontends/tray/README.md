# Kvist tray

A small Electron frontend: one icon for every Kvist running in a terminal, placed **inside your status bar**.

- The icon turns **green** when a turn finished in a terminal you are not looking at, and **amber** while one runs. It is hidden while no Kvist is running.
- A click opens a **read-only mini chat** that unfolds from the icon, flush against the bar and above other windows. A second click closes it. The **Go to terminal** button raises the terminal window (switching monitor and workspace) so you continue in the TUI.
- **Turn off icon** (in the panel, or the tray menu) runs `kvist --tray-op disable`: it saves `tray_enabled: false` in `~/.kvist/config.json` and removes the module from the bar. `kvist --tray-op enable|disable|status` does the same from a shell.

The icon is on by default. A machine without this frontend installed simply has no icon.

## The module in the bar

A program cannot draw over a Wayland bar (the bar is on a layer above ordinary windows), so the icon is a module of the bar itself. At every start a terminal core runs `kernel/tray_bar.py`, which adds the module to the configs it finds:

| Bar | Config | Placement |
|---|---|---|
| Waybar | `~/.config/waybar/config` (or `config.jsonc`) plus colors in `style.css` | `custom/kvist` after the first entry of `modules-left`; reload with `SIGUSR2` |
| Polybar | `~/.config/polybar/config.ini` | `[module/kvist]` after the first module of the first bar's `modules-left`; reload with `polybar-msg cmd restart` |

The edit is idempotent: a config that already names the module is never changed again, however many cores start (a file lock covers simultaneous starts), and the first version of each file is kept as `<file>.kvist-backup`. A Polybar that inherits `modules-left` on a second monitor shows the icon on both. Other bars (i3bar, KDE, GNOME, ...) are not edited; the daemon then shows a system tray icon instead, and never both.

The module runs `kvist --tray-op bar-stream` (prints the state) and `kvist --tray-op toggle` (the click). The panel is placed against the bar found under the pointer at the click: a Hyprland layer surface (`hyprctl layers`), an X11 dock window (`xdotool`, `xprop`), or the work-area strip of the display.

## How it works

`kernel/tray_link.py` runs in every core that lives in a terminal (the classic REPL and the Ink app's core; cores started by the Electron GUI, ACP or `run_task.py` are not terminal hosts). At the start and end of each turn it sends a state snapshot to the daemon over loopback TCP. The daemon publishes its port and a random token in `~/.kvist/tray.json` (mode 0600); a connection without the token is closed. The wire format is in [UI_API.md](../../docs/UI_API.md#8e-the-tray-link).

If no daemon answers, the first core to notice starts it, once per process. The daemon's single-instance lock makes simultaneous starts from several terminals produce one daemon. The daemon exits 30 seconds after the last core disconnects.

The transcript comes from `kvist --session-op history`, run with the same core command that started the daemon (`KVIST_CORE_CMD`). The mini chat never writes to a session.

## Terminal focus

The core's process ancestors (core, shell, terminal emulator) are matched against window owners; the nearest one with a window is raised.

| Desktop | Needs |
|---|---|
| Hyprland | `hyprctl` |
| i3 | `xdotool`, `i3-msg` |
| other X11 | `xdotool` |
| KDE Wayland | `kdotool` |
| macOS | `osascript` (best effort, not verified on a Mac) |
| Windows | PowerShell (best effort) |

A terminal that serves several windows from one process (for example `foot --server`) cannot be matched by process id; the chat then says so.

Verified on i3 with Polybar. The Waybar and Hyprland paths are covered by tests on sample data but not run against a live Hyprland session.

## Development

```bash
cd frontends/tray
npm install
npm test
npm start          # runs the daemon in the foreground
python scripts/make_icons.py   # regenerate assets/ (needs Pillow)
```

A core finds the daemon through `KVIST_TRAY_CMD` (a JSON argv) or this directory's `node_modules/electron`. The Python tests set `KVIST_TRAY_CMD` to a command that exits at once, so no test starts a real daemon.
