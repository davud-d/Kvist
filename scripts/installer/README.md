# scripts/installer/

Everything that puts Kvist onto a machine. Every script runs on Linux and
Windows (and, best-effort, macOS).

| Script | What it does |
| --- | --- |
| `install.py` | The front door: offers the installers below as an arrow-key list, all selected by default, and runs the ones you keep. |
| `install_rust.py` | Installs what Kvist Glass and the voice screen are built with: a C compiler (apt-get, dnf, zypper or pacman under root or a passwordless sudo; otherwise it prints the commands) and Rust through rustup's official installer (minimal profile; an installed Rust is left alone). Not offered on Windows: Glass needs kitty graphics, which Windows terminals do not have. |
| `install_cli.py` | Installs the `kvist` CLI launcher + standalone binary (builds it with PyInstaller if `dist/` has none). The build includes the Ink UI when npm is there and Kvist Glass (`cargo build --release` in `frontends/tui/glass`) when cargo is, and the voice screen (`cargo build --release` in `frontends/voice/screen`), on Linux and macOS; it waits for `install_rust.py` when both are chosen. Formerly at the repo root, then `install.py`. |
| `install_jetbrains.py` | Finds every installed JetBrains IDE and unpacks the **Kvist** plugin into each one's `plugins/` folder. |
| `install_vscode.py` | Finds VS Code, VS Code Insiders and VSCodium command-line launchers and installs the Kvist `.vsix` into each editor. |
| `install_gui.py` | Installs the Electron GUI as a desktop app: Start Menu + Desktop shortcut on Windows, hicolor icons + `.desktop` entry on Linux, `Kvist.app` copied into `~/Applications` on macOS. |
| `install_zed.py` | Registers the installed `kvist-core` as a Zed ACP external agent: one `agent_servers` member in Zed's settings file, comments and all else left alone. |
| `install_kvide.py` | Adds a Kvist commit-message generator to the kvide editor: copies the adapter (`frontends/kvide/`) next to `kvist-core` and writes kvide's user `generators.toml`, so a plain `kvide` shows the button. A user file Kvist did not write is never overwritten or removed. |
| `jsonc_settings.py` | Surgical editor for one member of a JSONC document. Pure text surgery; `install_zed.py` is its only caller. |
| `_common.py` | Shared helpers: the quiet log + spinner every installer reports through, safe ZIP extraction, JetBrains IDE discovery. |

The release ships its own dependency-free twins of these installers, `scripts/release/install.sh` and `install.ps1`: they download the release assets and install the CLI binary, the JetBrains plugin and the VS Code extension (no Zed, no GUI). `install.sh` runs on Linux and, best-effort, macOS (the system bash 3.2 is enough); on a Mac it picks `kvist-macos-arm64` or `kvist-macos-x64` by CPU. `plugin-release.yml` attaches them to the rolling release; CI smoke-tests both. `install.sh` also has a screen mode: with a terminal it downloads the agent and a pinned Node.js (SHA-256 checked) into a temporary folder and starts `kernel --installer`, which opens the installer screen of the Ink bundle (`frontends/tui/ink/setup`, steps `components` and `installing`) and then the first-run setup. The screen only drives the script: `install.sh --plan` prints per-component facts as JSON (paths, commands, availability), `install.sh --only <ids> --events` installs and reports one JSON object per line, and `--cache <dir>` reuses assets the bootstrap already fetched. `KVIST_INSTALL_PLAIN=1` skips the screen. Components: `cli`, `node`, `ffmpeg` (a pinned static build for the preview video; Linux x86_64 and macOS), `jetbrains`, `vscode`, `path` (the PATH line is off unless chosen, and is added once) and, under a folding row, the build tools `python`, `libpython`, `binutils` (ticked in the screen from the start, never part of the script's own defaults): the system packages `install_cli.py` needs, installed with apt-get, dnf, zypper or pacman under root or a passwordless sudo (the screen runs `sudo -v` first when a password is needed; with no sudo the commands are only printed).

```bash
python scripts/installer/install.py                      # pick with the arrow keys
python scripts/installer/install.py --all                # everything, no questions
python scripts/installer/install.py --cli --gui          # named components, no questions

python scripts/installer/install_rust.py                 # Rust + a C compiler, for Kvist Glass
python scripts/installer/install_cli.py                  # or run one on its own
python scripts/installer/install_jetbrains.py            # --build to build the plugin ZIP first
python scripts/installer/install_jetbrains.py --uninstall
python scripts/installer/install_vscode.py               # --build to build the VSIX first
python scripts/installer/install_vscode.py --uninstall
python scripts/installer/install_gui.py                  # --build to run electron-builder first
python scripts/installer/install_gui.py --uninstall
python scripts/installer/install_zed.py                  # needs install_cli.py to have run first
python scripts/installer/install_zed.py --uninstall
python scripts/installer/install_kvide.py                # needs install_cli.py to have run first
python scripts/installer/install_kvide.py --uninstall
```

## Output

Each installer is quiet by default: the commands it spawns (pip, PyInstaller,
npm, Gradle, vsce) write into its own log under the temp dir while a single
spinner line names the current step, and a failure prints the tail of that log
plus its path. Pass `--verbose` to any of them to watch those commands directly
instead, with no spinner and no log. `install_zed.py` and `install_kvide.py` spawn nothing — it edits
one file — so it prints its steps directly and `--verbose` only adds the entry
it wrote.

Run through `install.py`, the chosen installers run at once as background
processes and a live panel shows the step each one is on; `install.py --verbose`
drops the panel and runs them one after another. A component that depends on
another waits for it: the Zed registration and the kvide generator stay queued until the CLI installer
has put the binary in place.

## How JetBrains IDEs are discovered

Since 2020.1 every IDE keeps its per-user data under one vendor root:

* Linux: `~/.local/share/JetBrains/<Product><version>`
* Windows: `%APPDATA%\JetBrains\<Product><version>`
* macOS: `~/Library/Application Support/JetBrains/<Product><version>`

`install_jetbrains.py` enumerates those product directories (IntelliJ IDEA,
PyCharm, WebStorm, GoLand, CLion, Rider, RubyMine, DataGrip, RustRover, Android
Studio, …) and installs into `.../<Product><version>/plugins/`. Restart the IDE
afterwards.

On Linux, plugins go directly into `.../<Product><version>/`, without a `plugins/` subdirectory. Discovery also checks `~/.config/JetBrains` (or `$XDG_CONFIG_HOME/JetBrains`) for newly launched IDEs whose plugin data directory does not exist yet, and maps them to `~/.local/share/JetBrains` (or `$XDG_DATA_HOME/JetBrains`). Product versions are not restricted to particular years.

## How VS Code editors are discovered

`install_vscode.py` looks for command-line launchers on `PATH`: `code`,
`code-insiders` and `codium` (with `.cmd` variants on Windows). On Windows it
also checks the usual per-user and Program Files install locations. It installs
the built VSIX with:

```bash
code --install-extension <kvist.vsix> --force
```

Use `--build` to rebuild the bundled Kvist binary and VSIX before installing, or
`--vsix path/to/file.vsix` to install a specific package.

Without either flag the VS Code and JetBrains installers still rebuild when the
package they would install is older than the code it is built from (the core,
`kvist.spec`, the plugin and the Ink modules its webview imports; tests and
build output do not count), so an old build is never installed by accident.

The VS Code installer also lists `kvist.kvist` in the editor's `argv.json`
(`enable-proposed-api`), which VS Code requires before it will draw a correction
with the old text struck through. Only that one entry is added: the other flags,
comments and layout stay, the original is copied once to `argv.json.kvist-backup`,
a file that already lists the extension is not written, and a file it cannot edit
safely is left alone with a warning. Uninstalling leaves the entry (it does
nothing without the extension). Quit VS Code completely and start it again
afterwards; Reload Window does not re-read `argv.json`.

## How Zed is configured

Zed reaches coding agents through the Agent Client Protocol and finds them under
`agent_servers` in its settings file. `install_zed.py` writes exactly one member
of that object and nothing else:

```json
{
  "agent_servers": {
    "Kvist": {
      "type": "custom",
      "command": "/home/you/.local/share/kvist/kvist-core",
      "args": ["--acp"],
      "env": {
        "XDG_CONFIG_HOME": "/home/you/.local/share/kvist/acp-pristine-config"
      }
    }
  }
}
```

The command is the standalone binary `install_cli.py` installs — not the `kvist`
wrapper, which forces `--workspace "$PWD"` and is a shell script Windows cannot
exec. Run `install_cli.py` first: `install_zed.py` edits settings only and
refuses to write an entry pointing at a binary that is not there.

`XDG_CONFIG_HOME` is what keeps the user's own shell startup off the agent's
stdout — Zed wraps the command in the login shell, and a startup that prints
anything makes Zed reject the stream. `install_zed.py` creates that directory
next to the binary; the mechanism is in
[`frontends/zed/README.md`](../../frontends/zed/README.md).

Zed's settings are JSONC, so the edit replaces exactly the character range of
that one member (`jsonc_settings.py`). Comments, key order and line endings
survive, and the file is rewritten atomically with the permissions it had.
Before the first change it is copied to `settings.json.kvist-backup` — once, so
a later run cannot overwrite the user's own edits since then.

Zed documents `~/.config/zed/settings.json` on macOS and on Linux without a
custom `XDG_CONFIG_HOME`, and re-reads the file on its own ("You do not need to
restart Zed."). It does **not** document a Windows location — only that settings
live in the user profile — so on Windows the installer checks the candidates it
can name and, when none exists, stops and reports them rather than inventing a
path. Pass `--settings` to point it at the file Zed actually uses.

The parity the Zed integration does and does not have is written down in
[`frontends/zed/README.md`](../../frontends/zed/README.md).

## Icons (`branding/`)

The installers read the icons from the repository's `branding/` directory.
`icon.svg` is the master mark (cyan chevron + cursor bar on a slate rounded
square). `icon-<n>.png` are flat renders; `kvist.ico` is the multi-resolution
Windows icon. Regenerate from the SVG with:

```bash
cd branding/
for s in 16 24 32 48 64 128 256 512; do rsvg-convert -w $s -h $s icon.svg -o icon-$s.png; done
magick icon-256.png -define icon:auto-resize=256,128,64,48,32,16 kvist.ico
```

The GUI build copies of these live in `frontends/gui/electron/build/` and are wired into
`frontends/gui/electron/package.json` (`build.linux.icon`, `build.win.icon`).
