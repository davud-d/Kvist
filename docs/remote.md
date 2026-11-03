# Kvist on a server

Kvist can do its work on another machine and keep going when you are gone: close the interface, switch
the computer off, come back tomorrow, connect, and see what the agent did meanwhile. The agent runs on the
server as a service; your interface reaches it over ssh.

```
interface ── ssh ──> server: kvist daemon (a service) ── writes every event to a log file
   (any computer)            └─ the agent works whether or not anyone is connected
```

## What you need

- A Linux, macOS or BSD server with Kvist installed (`~/.local/share/kvist/kvist-core`, which the installer
  puts there) and a provider set up there (`ssh -t HOST kvist-core --setup` once).
- Key login over ssh (ssh-agent or a key without a prompt). Passwords cannot be typed: the connection is
  made without a terminal, and Kvist never asks ssh a question. The first time, run `ssh HOST` yourself once
  so the host key is accepted.
- The interface on your computer needs only `ssh`. The server cannot be Windows.

## Start

```
kvist --remote me@server --remote-workspace /home/me/project
```

Kvist asks the server first: is Kvist there, does it speak this version, is the folder real, is a provider
set up. If the agent is not running for that folder, it offers to **install it as a service** (recommended:
it starts again after a reboot and after a crash), to start it once, or to cancel. Then the interface opens as
usual. `--frontend` chooses which (`tui`, `claude`, `opencode`, `glass`, `voice`). In the Ink interfaces the
command `/remote user@host /folder` does the same from inside, and `/remote off` comes back to this machine.

`user@host:2222` names a port. Names from `~/.ssh/config` work.

## What happens when you leave

The agent keeps working. Quit the interface, close the lid, lose the network: the daemon on the server does not
care. A question it needs answered (an approval, a plan) waits for you. Scheduled wake-ups and finished
background jobs run their turns on their own.

When you connect again, the interface is redrawn from the daemon's event log: finished turns as they ended, the
turn in progress as it stands, dialogs that are still open. A break in the middle of a session (a flaky link,
the server rebooting) is handled by the proxy that runs on your side: it reconnects with 1, 2, 4 … 30 second
pauses and continues exactly where it stopped, without repeating anything. It gives up after 10 minutes, or
at once when waiting cannot help (the host key is unknown, ssh login is refused, Kvist is not installed, no
service is running), and says why. It never knocks for ever on a Kvist that is not there.

## Only one writes

Several interfaces may look at the same session, but only one **drives**. The first to connect drives; any
other shows a banner in place of the input line, *"another frontend is driving this session · Enter — take
over"*. The server drops whatever an observer sends, so two interfaces can never put two tasks into one session.
Enter on the banner takes over; the previous driver becomes an observer at once. Dialogs still waiting are
sent to whoever takes over.

If the driver's computer lost power, its role passes by itself after 20 seconds without a sign of life, and
the next interface to ask gets it.

## The service

| System | What is installed | Starts |
|---|---|---|
| Linux with systemd | a user unit `kvist-<id>.service` (`Restart=on-failure`) | at boot if lingering is on (`loginctl enable-linger`, which Kvist tries and otherwise tells you to run with `sudo`), else at login |
| macOS | a LaunchAgent `site.kvist.remote.<id>` | when you log in to the Mac, not at boot |
| BSD, Linux without systemd | an `@reboot` line in your crontab | at boot; **not restarted after a crash** |

`kvist --remote-op install-service|uninstall-service|start|stop|status|probe --remote HOST --remote-workspace PATH`
manage it. `start` runs the daemon once without a service. One daemon serves one folder.

After a reboot or a crash the daemon starts with a fresh log and says so in the feed. The task that was
running is not continued by itself (no edit is repeated unseen): `/resume` continues it.

## Files on the server

`~/.kvist/remote/<id>/` (or under `KVIST_CONFIG_DIR`), private to you: `events.ndjson` (everything the agent
told its interface, `.1` is the previous one), `sock` (the socket interfaces connect to, readable by you only),
`daemon.lock`, `daemon.json`, `daemon.log`. The log is replaced when the daemon starts and when it passes 64 MB at
the end of a turn with nobody connected. The conversation itself is in the folder's `.kvist/sessions/` as always.

## Interfaces

| Interface | State |
|---|---|
| Ink (`tui`, `claude`) | works: banner, take over, link state, `/remote`; seen in a real terminal against the real core |
| OpenCode terminal | banner and take over from the shared code; tested, not seen in a terminal; no `/remote` |
| VS Code | settings `kvist.remote.host` and `kvist.remote.workspace`; banner and take over in the panel; not run in VS Code |
| JetBrains | handles the take over message; no setting to choose a server; not run |
| Classic (`glass`), Voice | banner (Voice: Enter takes over, speech and typing are refused meanwhile); tested, not seen in kitty |
| Pixel app (Tauri), Electron | show the banner and take over; no way to choose a server from them |
| Classic Legacy, Zed (ACP) | not supported: the agent runs inside them |

## Limits, honestly

- Windows is not a server. A Windows computer as the *interface* only needs `ssh.exe`; it has not been tried.
- macOS and BSD code paths (LaunchAgent, crontab, `AF_UNIX` paths) are covered by text-level tests only and
  were never run on those systems.
- Approvals that nobody is there to answer wait for ever. Auto mode (`/permissions auto on`) decides them
  without you, with the same warning as everywhere.
- The interface's own settings screens (providers, keys) still edit the configuration of your computer, not of the
  server. Change the server's with `ssh -t HOST kvist-core`.
- `--resume`, `--role`, `--provider` and the model overrides do not reach a running daemon; it keeps what it
  started with.

Protocol details for interface authors: [UI_API.md](UI_API.md), section 8g.
