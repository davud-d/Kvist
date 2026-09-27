# installer/

Everything that puts Kvist onto a machine. Every script runs on Linux and
Windows (and, best-effort, macOS).

| Script | What it does |
| --- | --- |
| `install.py` | The front door: offers the five installers below as an arrow-key list, all selected by default, and runs the ones you keep. |
| `install_cli.py` | Installs the `kvist` CLI launcher + standalone binary (builds it with PyInstaller if `dist/` has none). Formerly at the repo root, then `install.py`. |
| `install_jetbrains.py` | Finds every installed JetBrains IDE and unpacks the **Kvist Launcher** plugin into each one's `plugins/` folder. |
| `install_vscode.py` | Finds VS Code, VS Code Insiders and VSCodium command-line launchers and installs the Kvist `.vsix` into each editor. |
| `install_gui.py` | Installs the Electron GUI as a desktop app: Start Menu + Desktop shortcut on Windows, hicolor icons + `.desktop` entry on Linux. |
| `install_zed.py` | Registers the installed `kvist-core` as a Zed ACP external agent: one `agent_servers` member in Zed's settings file, comments and all else left alone. |
| `jsonc_settings.py` | Surgical editor for one member of a JSONC document. Pure text surgery; `install_zed.py` is its only caller. |
| `_common.py` | Shared helpers: the quiet log + spinner every installer reports through, safe ZIP extraction, JetBrains IDE discovery. |

```bash
python installer/install.py                      # pick with the arrow keys
python installer/install.py --all                # everything, no questions
python installer/install.py --cli --gui          # named components, no questions

python installer/install_cli.py                  # or run one on its own
python installer/install_jetbrains.py            # --build to build the plugin ZIP first
python installer/install_jetbrains.py --uninstall
python installer/install_vscode.py               # --build to build the VSIX first
python installer/install_vscode.py --uninstall
python installer/install_gui.py                  # --build to run electron-builder first
python installer/install_gui.py --uninstall
python installer/install_zed.py                  # needs install_cli.py to have run first
python installer/install_zed.py --uninstall
```

## Output

Each installer is quiet by default: the commands it spawns (pip, PyInstaller,
npm, Gradle, vsce) write into its own log under the temp dir while a single
spinner line names the current step, and a failure prints the tail of that log
plus its path. Pass `--verbose` to any of them to watch those commands directly
instead, with no spinner and no log. `install_zed.py` spawns nothing — it edits
one file — so it prints its steps directly and `--verbose` only adds the entry
it wrote.

Run through `install.py`, the chosen installers run at once as background
processes and a live panel shows the step each one is on; `install.py --verbose`
drops the panel and runs them one after another. A component that depends on
another waits for it: the Zed registration stays queued until the CLI installer
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
[`zed-acp/README.md`](../zed-acp/README.md).

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
[`zed-acp/README.md`](../zed-acp/README.md).

## `assets/`

`icon.svg` is the master mark (cyan chevron + cursor bar on a slate rounded
square). `icon-<n>.png` are flat renders; `kvist.ico` is the multi-resolution
Windows icon. Regenerate from the SVG with:

```bash
cd installer/assets
for s in 16 24 32 48 64 128 256 512; do rsvg-convert -w $s -h $s icon.svg -o icon-$s.png; done
magick icon-256.png -define icon:auto-resize=256,128,64,48,32,16 kvist.ico
```

The GUI build copies of these live in `gui/build/` and are wired into
`gui/package.json` (`build.linux.icon`, `build.win.icon`).
