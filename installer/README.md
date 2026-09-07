# installer/

Everything that puts Kvist onto a machine. Every script runs on Linux and
Windows (and, best-effort, macOS).

| Script | What it does |
| --- | --- |
| `install.py` | The front door: offers the three installers below as an arrow-key list, all selected by default, and runs the ones you keep. |
| `install_cli.py` | Installs the `kvist` CLI launcher + standalone binary (builds it with PyInstaller if `dist/` has none). Formerly at the repo root, then `install.py`. |
| `install_jetbrains.py` | Finds every installed JetBrains IDE and unpacks the **Kvist Launcher** plugin into each one's `plugins/` folder. |
| `install_gui.py` | Installs the Electron GUI as a desktop app: Start Menu + Desktop shortcut on Windows, hicolor icons + `.desktop` entry on Linux. |
| `_common.py` | Shared helpers: the quiet log + spinner every installer reports through, safe ZIP extraction, JetBrains IDE discovery. |

```bash
python installer/install.py                      # pick with the arrow keys
python installer/install.py --all                # everything, no questions
python installer/install.py --cli --gui          # named components, no questions

python installer/install_cli.py                  # or run one on its own
python installer/install_jetbrains.py            # --build to build the plugin ZIP first
python installer/install_jetbrains.py --uninstall
python installer/install_gui.py                  # --build to run electron-builder first
python installer/install_gui.py --uninstall
```

## Output

Each installer is quiet by default: the commands it spawns (pip, PyInstaller,
npm, Gradle) write into its own log under the temp dir while a single spinner
line names the current step, and a failure prints the tail of that log plus its
path. Pass `--verbose` to any of them to watch those commands directly instead,
with no spinner and no log.

Run through `install.py`, the chosen installers run at once as background
processes and a live panel shows the step each one is on; `install.py --verbose`
drops the panel and runs them one after another.

## How JetBrains IDEs are discovered

Since 2020.1 every IDE keeps its per-user data under one vendor root:

* Linux: `~/.local/share/JetBrains/<Product><version>`
* Windows: `%APPDATA%\JetBrains\<Product><version>`
* macOS: `~/Library/Application Support/JetBrains/<Product><version>`

`install_jetbrains.py` enumerates those product directories (IntelliJ IDEA,
PyCharm, WebStorm, GoLand, CLion, Rider, RubyMine, DataGrip, RustRover, Android
Studio, …) and installs into `.../<Product><version>/plugins/`. Restart the IDE
afterwards.

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
