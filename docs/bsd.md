# Kvist on FreeBSD, OpenBSD and NetBSD

**Experimental.** The core and the classic terminal are written to run there, but nobody has run them on a real BSD
yet: the only check is the `bsd` job of `.github/workflows/platforms.yml`, which boots each system in QEMU and runs
the credential-free smoke tests. Treat the first runs as a report on what still breaks, and send what you see.

## Install (from source)

There is no release binary: PyInstaller does not build on the BSDs, so `install.sh` stops with a pointer here.
Packages with native code have no BSD wheels, so they come from the system's own packages (the names below are the
ones the CI job uses).

```sh
# FreeBSD 15
sudo pkg install -y python312 py312-pip py312-sqlite3 py312-pillow py312-cryptography py312-pydantic2 \
    py312-pydantic-core py312-tiktoken py312-jiter py312-keyring py312-paramiko git ripgrep
# OpenBSD 8
doas pkg_add python py3-pip py3-Pillow py3-cryptography py3-pydantic py3-pydantic-core py3-tiktoken py3-jiter \
    py3-keyring py3-paramiko git ripgrep
# NetBSD 11 (tiktoken and jiter have no package: pip builds them, which needs rust)
sudo pkgin -y install python312 py312-pip py312-Pillow py312-cryptography py312-pydantic py312-pydantic-core \
    py312-keyring py312-paramiko git-base ripgrep rust

python3 -m venv --system-site-packages .venv      # python3.12 / python3.14 where the command has a version
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m kernel --workspace /path/to/project
```

`kvist --doctor` (or `/doctor`) lists what is missing.

## What differs from Linux

- **No OS sandbox.** Bubblewrap is Linux-only and the BSDs have no cgroups. In the default `auto` mode a command that
  runs code (a script, `python -c`, `make`, a test runner) is therefore refused. `AGENT_SANDBOX_MODE=workspace` allows
  it: that is **not** an isolation layer, an approved command runs with your own rights, and Kvist says so at start.
  Reading and editing files, `git`, `rg` and the other commands that do not run code work without it.
- **Network cannot be cut off a command.** Every approval carries a note saying so.
- **Stopping a command** kills its process group, and a process that left the group (`setsid`) is found by listing
  processes with `ps` (the Linux core reads `/proc` and uses a cgroup instead). This is best effort.
- **Package directories.** `/usr/local/bin` (FreeBSD, OpenBSD) and `/usr/pkg/bin` (NetBSD) are on the child PATH when they
  are root-owned and not writable by anyone else, so `git`, `python3` and `rg` are found.
- **Not available:** skills that run code (they need Bubblewrap), the prebuilt installer and binaries, the system tray,
  the voice interface. The Ink, Glass and OpenCode terminals, the desktop apps and the IDE plugins were not tried.

## Later

A real sandbox per system (`pledge`/`unveil` on OpenBSD, Capsicum or a jail on FreeBSD) would let `auto` mode run code
there too. It is a separate, security-sensitive piece of work that needs the systems themselves to test.
