# Kvist IntelliJ Plugin

IntelliJ Platform plugin for Kvist: a chat tool window with the same panel as the VS Code extension, a launcher that starts the Kvist binary inside the IDE Terminal for the currently opened project, commit messages, and ghost-text inline completion.

## Chat tool window

The **Kvist** tool window (right side) shows the VS Code extension's chat panel in the IDE's embedded browser (JCEF, `JBCefBrowser`). The panel is not copied: the Gradle build runs the extension's own `npm run build` in `../vscode-extension` and packs `out/webview.js`, `out/webview.css` and the logo into the plugin as resources (`buildWebview` and `syncWebview` tasks). So the build needs Node.js 22+ and npm; it runs `npm ci` there when `node_modules/` is missing.

- The page talks to the plugin through one `JBCefJSQuery`. `vscode-extension/webview/host.ts` is the only place in the panel that knows which IDE it runs in.
- The plugin starts the bundled core as a child process in mode B (`--frontend stdio-multi`, UI_API.md) and passes NDJSON between it and the page. The first event must be `hello` with `protocol_version` 1. Approvals are fail-closed: open requests are denied when the core stops.
- Title buttons: New Session, Settings, Reload (a new page and a new core, like Reload Window in VS Code; also how an exited core is restarted) and Run Kvist in the terminal.
- Environment variables for the core (Settings in the panel) are stored in the IDE's application properties in plain text. Keep API keys in the provider settings.
- Without JCEF (a boot runtime without it, or JCEF turned off) the tool window says so and offers the terminal mode instead. **Run Kvist** in the toolbar and the Tools menu works as before.

## Commit messages

The commit dialog (and the non-modal Commit tool window) gets a Kvist button next to the message field. It sends the changes ticked in the dialog, partially staged lines included, plus the project's last 15 commit subjects to `kvist --commit-message`, and puts the answer into the message field.

While it runs, the button shows a spinner; hovering turns it into a red stop square, and clicking it then cancels the generation instead of starting another one.

- The message is always English. Its style follows the project's history: Conventional Commits if the project uses them, plain imperative sentences otherwise. A body is added only when the diff does not explain itself.
- It uses the provider and **fast** model saved in `~/.kvist/config.json`. Run Kvist once in a terminal to configure them; the button never asks for a key.
- Secret files (`.env`, keys, certificates, ...) and `.kvist/` are dropped before anything reaches the model.
- The feature needs the bundled Git plugin. With Git disabled, only the launcher is available.
- The bundled binary must be built from a Kvist version that has `--commit-message`; rebuild it with the scripts below.

## Inline completion

As you type, the editor shows a gray-text ("ghost text") suggestion from `kvist --complete`, accepted with Tab like any other inline completion. Toggle it from **Tools | Kvist Inline Completion**; it is on by default.

- One short-lived `kvist --complete` process per suggestion, independent of the chat tool window's core process: it runs even while an agent turn is in progress, and never waits behind one.
- The IDE's own debounce-and-cancel mechanism for inline completion providers is reused as-is: a short pause after typing before a request fires, and a further keystroke cancels whatever request is still in flight (and kills its process) instead of queuing behind it.
- `prefix`/`suffix` are the live, unsaved buffer split at the caret — never a save to disk first.
- Only Python and JS/TS variants get their imports resolved into extra context by the core; every other language still gets a plain completion.
- The bundled binary must be built from a Kvist version that has `--complete`; rebuild it with the scripts below.

## Compatibility

This MVP is intentionally pinned to IntelliJ Platform 2025.3.

It uses the reworked Terminal API, which is the reason the plugin is limited to the 253 platform line.

## Supported platforms

- Windows
- Linux

macOS is intentionally unsupported in this MVP.

## Binaries

Put the real binaries here before running packaging or sandbox tasks:

- bin/win/kvist.exe
- bin/linux/kvist.bin

If any required binary is missing, the build fails with a clear error.

The plugin ZIP always expects both files to exist.

The helper scripts below rebuild only the binary for the current host OS:

- Linux host -> refreshes `bin/linux/kvist.bin`
- Windows host -> refreshes `bin/win/kvist.exe`

So for a full distributable ZIP, keep the binary for the other OS in `bin/` as well.

## Build

Use JDK 21.

You also need Python with project dependencies installed and `PyInstaller` available in that environment.

## Quick build with OS-specific scripts

These scripts do the fast path for local packaging:

1. build the Kvist binary for the current OS with `PyInstaller`
2. copy it into `jetbrains-plugin/bin/...`
3. refresh the IntelliJ sandbox copy if it already exists
4. run the Gradle plugin build

Run them from the `jetbrains-plugin/` directory.

### Linux

```bash
./build-plugin.sh
```

What it updates:

- `bin/linux/kvist.bin`
- `build/distributions/*.zip`

### Windows (PowerShell)

```powershell
.\build-plugin.ps1
```

What it updates:

- `bin/win/kvist.exe`
- `build\distributions\*.zip`

### Windows (Command Prompt)

```bat
build-plugin.bat
```

Use this if you prefer plain `cmd.exe` instead of PowerShell.

## Manual Gradle build

```bash
./gradlew buildPlugin
```

The plugin ZIP is created in build/distributions/.

On Windows use `gradlew.bat buildPlugin` instead.

This Gradle-only path does not rebuild the Kvist executable. It just packages whatever is already present in `bin/win/` and `bin/linux/`.

## Manual build by operating system

Use this path if you do not want to use the helper scripts and prefer running each step yourself.

Important:

- build the executable on the target OS itself
- keep the binary for the other supported OS in `bin/` as well if you want a distributable ZIP
- run the PyInstaller step from the repository root, not from `jetbrains-plugin/`

### Manual build on Linux

1. From the repository root, build the Linux executable with PyInstaller:

```bash
python -m PyInstaller kvist.spec
```

2. Copy the result into the plugin directory:

```bash
install -Dm755 dist/kvist jetbrains-plugin/bin/linux/kvist.bin
```

3. Make sure the Windows binary is already present:

- `jetbrains-plugin/bin/win/kvist.exe`

4. Build the plugin ZIP:

```bash
cd jetbrains-plugin
./gradlew clean buildPlugin
```

Result:

- `jetbrains-plugin/build/distributions/*.zip`

### Manual build on Windows

1. From the repository root, build the Windows executable with PyInstaller:

```powershell
python -m PyInstaller kvist.spec
```

2. Copy the result into the plugin directory:

```powershell
Copy-Item dist\kvist.exe jetbrains-plugin\bin\win\kvist.exe -Force
```

3. Make sure the Linux binary is already present:

- `jetbrains-plugin/bin/linux/kvist.bin`

4. Build the plugin ZIP:

```powershell
cd jetbrains-plugin
.\gradlew.bat clean buildPlugin
```

Result:

- `jetbrains-plugin\build\distributions\*.zip`

## Typical workflows

### Fast local Linux build

1. Make sure `bin/win/kvist.exe` is already present.
2. Run `./build-plugin.sh`.
3. Pick the ZIP from `build/distributions/`.

### Fast local Windows build

1. Make sure `bin/linux/kvist.bin` is already present.
2. Run `.\build-plugin.ps1` or `build-plugin.bat`.
3. Pick the ZIP from `build\distributions\`.

## Run sandbox IDE

```bash
./gradlew runIde
```

On Windows use gradlew.bat instead of ./gradlew.

## Verify installed ZIP

1. Build the plugin ZIP with ./gradlew buildPlugin.
2. Open a clean IDE or the Gradle sandbox started by ./gradlew runIde.
3. Install the ZIP from build/distributions/ using Settings | Plugins | gear icon | Install Plugin from Disk.
4. Open any project.
5. Run Kvist from the toolbar or Tools menu.
6. Confirm that the IDE Terminal starts:

```text
kvist --workspace "<project_path>"
```

## Supported operating systems

- Windows
- Linux

macOS is intentionally unsupported.