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
  puts there). It needs **no provider of its own**: the server works with the provider you have on this computer (see
  *Your provider* below).
- Key login over ssh (ssh-agent or a key without a prompt). Passwords cannot be typed: the connection is
  made without a terminal, and Kvist never asks ssh a question. The first time, run `ssh HOST` yourself once
  so the host key is accepted.
- The interface on your computer needs an ssh client: `ssh` (OpenSSH) on Linux, macOS and BSD, and on Windows either
  OpenSSH or **PuTTY** (0.77 or newer, with `plink` and `puttygen`; see *PuTTY on Windows*). The server cannot be Windows.

## Start

```
kvist --remote me@server --remote-workspace /home/me/project
```

The interface opens at once, showing `machine:/folder` where it works, and **asks its questions itself**, in its own dialogs: is Kvist
on the server and does it speak this version, is the folder real. If the agent is not running for that folder, it
offers to **install it as a service** (recommended: it starts again after a reboot and after a crash), to start it once, or to cancel. When the
machine cannot be made ready you choose: try again, work on this computer (the interface starts again here), or close. Nothing is asked on the
terminal behind the interface. `--frontend` chooses which interface (`tui`, `claude`, `opencode`, `glass`, `voice`). In the interfaces,
`/remote user@host /folder` does the same from inside, and `/remote off` comes back to this machine.

`user@host:2222` names a port. Names from `~/.ssh/config` work.

## Saved machines

```
/machines                 # in the interface; or, without one: kvist --machines
```

A dialog saves the machines you work on, so you do not type addresses again. For each one it keeps the address
(`login@host`, an IP, or a name from `~/.ssh/config`), the port, a key file, and a folder to work in. One machine can
be **main**: plain `kvist` then opens on it (`kvist --local` opens on this computer instead; if the main machine
cannot be used Kvist says why and opens here). Choosing **This computer** in the dialog clears the main machine, so the next
plain `kvist` stays here too. `kvist --machine NAME [--remote-workspace FOLDER]` names one.

**Every start opens a new session**, here and on a server, like a local launch. An agent on a server keeps its session while interfaces come and
go, so the interface asks it for a new one when it connects. **Work is never hidden by it**: a turn that is still running is not interrupted, and when the agent
finished work while nobody was connected, the next interface opens in that session, once, to show what it did. It stays in an old session also when
you asked to go on with it: `--resume`, or a session chosen under *Recent sessions*. Older ones are in `/session`.

Signing in, when you add a machine:

- **Host key.** Kvist fetches the machine's key and shows its fingerprint; you confirm it once and it goes to
  `~/.ssh/known_hosts` (with PuTTY, to a file of Kvist's own). No more running `ssh HOST` by hand first.
- **As ssh does** (agent, `~/.ssh/config`), or **a key file**: tried at once.
- **A password**, once. Kvist makes a key for that machine under `~/.kvist/ssh/`, signs in with the password (it
  exists only in the environment of that one ssh process), puts the key in the machine's `authorized_keys`,
  proves it works and forgets the password. Nothing with a password in it is ever written. The password path
  needs OpenSSH 8.4 or newer on your side and has not been run against a server that asks for a password.

The **folder** is per machine: leave it empty for `~/Kvist Workspace` (Kvist offers to create it) or name any
other, and pick another one each time you connect. **A session belongs to the machine it was made on**: it lives in
that folder's `.kvist/sessions` there. *Recent sessions* in the dialog lists sessions from every machine and opens
one where it lives. What you change inside a session (`/models`, `/effort`, `/permissions`) changes
that machine's Kvist; with `--machine` the settings screens and `--config-op`, `--skill-op`, `--mcp-op` and the
like also act on that machine, not on this computer. The dialog is **drawn by the interface you are in**: Kvist asks, the interface shows its own lists and fields (the
terminal ones, OpenCode's, VS Code's, and so on) and nothing is asked in a separate window. When you have chosen, the interface closes and
opens again on that machine (in VS Code the panel starts its core there). An interface that cannot do this says how to start Kvist on that
machine instead. `kvist --machines` is the same dialog on the terminal, for when no interface is open.

The list is in `~/.kvist/machines.json` (private). It holds addresses, key paths and folders, never passwords.

## A server with no Kvist, or an old one

Remote work needs the remote core on **both** ends. When you connect, the interface asks the server first. If there is no Kvist there, or
the one there is too old to do remote work (an installed release that predates it refuses `--remote-op`), Kvist offers to **copy this
computer's own Kvist to it** (about 110 MB, over the same ssh) and carries on with it. Before it replaces anything the copy is run once on the
server (`--version`) from a temporary name: a program built for another kind of system (another processor, not Linux) is refused up front, and one
built against a newer C library than the server has fails that run and the server is left as it was, with the library error shown. What was there
is kept as `kvist-core.previous`. The question is asked in the interface you are in (or on the terminal at start-up) and nothing is copied
unattended.

A program built on a very new Linux may need a newer C library than a stable server such as Debian 12 has; a release build, made on an
older system, does not. From a source checkout there is no program to copy, so the sources are copied instead and set up there (Python 3.11 or
newer, and internet on the server; on Debian `apt install python3-venv` first). You can also do that by hand:

```
python scripts/install_on_machine.py NAME      # NAME is a machine saved with kvist --machines
```

It puts the sources in `~/kvist-src` and a small script as `~/.local/share/kvist/kvist-core`; the self-update, which only replaces a frozen binary,
leaves it alone. Run it again after changing the sources.

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

## Several sessions at the same time

An agent works on one session at a time, so for a second session beside it in the same folder Kvist starts **another agent process** on the server (a *lane*;
up to 8). When you open an interface and something is already going on there (a session that is working, one that finished while you were away, one open in another
window), the interface asks in its own dialog: **join** one of them, or start **a new session** that works beside them. A session you chose under *Recent sessions* is joined where it runs.
Nothing busy: you simply get a new session, as always. Only the first agent is a service that comes back after a reboot. An extra one ends by itself after 15 minutes with nobody
connected, nothing running, no unseen result, no stopped task and no timer, so they do not pile up in a small server's memory; the session itself stays on the disk. Two sessions in one folder can
change the same files, so give them different parts of the work.

## An agent that is older than the interface

The agent on a server keeps running after you update Kvist there, so a new interface can meet an old agent. Each side says which
revision of the interface-to-agent contract it speaks (`wire.REVISION`). Before opening, the interface checks both the installed Kvist and the
running agent: an installed Kvist that is too old gets the offer to copy this computer's Kvist; a running agent that is older gets **"Restart it?"**
in the interface's own dialog (it says how many other interfaces are connected, because a restart cancels the turn in progress). If you cannot
be asked, Kvist stops with the command to restart it by hand. It never connects silently to an agent that cannot do what you ask.

## Your provider

The server works with **the provider you have on this computer**: its name, its models and its key. When an interface connects to a
daemon that has none yet, it sends them over the ssh connection, and the daemon keeps them **in its memory only**. They are never
written to the server's disk (`config.json` there is not read for them and not changed), so a server that has its own provider
is not used for this: its own settings stay for running Kvist directly on it. What a session changes about the provider
(`/models`, `/provider`) is also kept in that memory. The cost is that a daemon that restarted (the server rebooted) does
not know the key until an interface connects again, which it does by itself. Only the active provider is sent, never the
other keys. When you pick **another provider inside a session** (`/provider`), the daemon asks the interface that is writing to
send that provider's settings from this computer (a key you only have here is not asked for again; a ChatGPT sign-in is never started in a
browser on the server). **ChatGPT** is lent as a short-lived access token only: its refresh token never leaves this computer (it is replaced
each time it is used, so a copy used on the server would sign you out here), and the interface sends a new access token before the old one runs
out, for as long as it stays connected. Left disconnected past the token's life, the server asks you to connect again. With no
provider here, the server's own provider is used if it has one. Custom providers: only the one in use is lent, so the others are not
in the `/provider` list while you work on a server. The settings screens and `--config-op` that act on the server
(`--machine`) read that machine's file and do not see the lent provider.

## Files on the server

`~/.kvist/remote/<id>/` (or under `KVIST_CONFIG_DIR`), private to you: `events.ndjson` (everything the agent
told its interface, `.1` is the previous one), `sock` (the socket interfaces connect to, readable by you only),
`daemon.lock`, `daemon.json`, `daemon.log`. The log is replaced when the daemon starts and when it passes 64 MB at
the end of a turn with nobody connected. The conversation itself is in the folder's `.kvist/sessions/` as always.

## Interfaces

| Interface | State |
|---|---|
| Ink (`tui`, `claude`) | works: banner, take over, link state, `/machines`, `/remote`; seen in a real terminal against the real core and a real sshd |
| OpenCode terminal | banner and take over from the shared code; tested, not seen in a terminal; no `/remote` |
| VS Code | settings `kvist.remote.host` and `kvist.remote.workspace`; banner and take over in the panel; not run in VS Code |
| JetBrains | handles the take over message; no setting to choose a server; not run |
| Classic (`glass`), Voice | banner (Voice: Enter takes over, speech and typing are refused meanwhile); tested, not seen in kitty |
| Pixel app (Tauri), Electron | show the banner and take over; no way to choose a server from them |
| Classic Legacy, Zed (ACP) | not supported: the agent runs inside them |

## PuTTY on Windows

Kvist uses whichever client it finds. On Windows it takes `plink` when PuTTY is installed and `ssh` otherwise; on every other system it
uses `ssh`. `KVIST_SSH` forces one: a program whose name starts with `plink` is run the PuTTY way, anything else the OpenSSH way.

- Connections run `plink -batch`: no terminal, no questions, as with ssh. Keys are PuTTY's `.ppk`; with **a password** Kvist makes one
  (with `puttygen`, no passphrase) and installs it, as above. The password reaches plink in a file (`-pwfile`, PuTTY 0.77+) that exists
  for that one call and is deleted at once, never on the command line.
- plink has no `known_hosts` and no `ssh-keyscan`. Kvist reads the fingerprint plink reports, you confirm it, and Kvist keeps it in
  `~/.kvist/ssh/putty_hosts.json` and passes it with `-hostkey` on every connection. PuTTY's registry is not touched, and a
  machine whose key changed is refused.
- `~/.ssh/config` is not read by plink, so *As ssh does* means: Pageant, or the default of the PuTTY version you have.

## Limits, honestly

- Windows is not a server. A Windows computer as the *interface* needs `ssh.exe` or PuTTY; the PuTTY path is checked in CI against
  the real `plink` and `puttygen` (a real sshd on Linux and macOS, the key tools on Windows), not yet by a person at a Windows machine.
- macOS and BSD code paths (LaunchAgent, crontab, `AF_UNIX` paths) run in CI against a real sshd on macOS, FreeBSD, OpenBSD
  and NetBSD (the BSD job is experimental); service installation itself is covered by text-level tests only.
- Approvals that nobody is there to answer wait for ever. Auto mode (`/permissions auto on`) decides them
  without you, with the same warning as everywhere.
- A login shell on the server that prints text on every ssh command (a banner, a system summary) is tolerated: Kvist
  skips what comes before its own lines. Printing on **stdin** would not be.
- `--resume`, `--role`, `--provider` and the model overrides do not reach a running daemon; it keeps what it
  started with.
- The key is in the daemon's memory: whoever can read that process as your user on the server can read it, and Kvist does
  not control the server's swap or core dumps.

Protocol details for interface authors: [UI_API.md](UI_API.md), section 8g.
