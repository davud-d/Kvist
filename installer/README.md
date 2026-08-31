# installer/

Everything that puts Kvist onto a machine. All three scripts run on Linux and
Windows (and, best-effort, macOS).

| Script | What it does |
| --- | --- |
| `install.py` | Installs the `kvist` CLI launcher + standalone binary (builds it with PyInstaller if `dist/` has none). Formerly at the repo root. |
| `install_jetbrains.py` | Finds every installed JetBrains IDE and unpacks the **Kvist Launcher** plugin into each one's `plugins/` folder. |
| `install_gui.py` | Installs the Electron GUI as a desktop app: Start Menu + Desktop shortcut on Windows, hicolor icons + `.desktop` entry on Linux. |

```bash
python installer/install.py
python installer/install_jetbrains.py            # --build to build the plugin ZIP first
python installer/install_jetbrains.py --uninstall
python installer/install_gui.py                  # --build to run electron-builder first
python installer/install_gui.py --uninstall
```

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
