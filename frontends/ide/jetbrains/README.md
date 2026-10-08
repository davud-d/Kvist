# Kvist IntelliJ Plugin

IntelliJ Platform plugin for Kvist: a chat tool window with the same panel as the VS Code extension, a launcher that starts the Kvist binary inside the IDE Terminal for the currently opened project, commit messages, and ghost-text inline completion.

## Chat tool window

The **Kvist** tool window (right side) shows the VS Code extension's chat panel in the IDE's embedded browser (JCEF, `JBCefBrowser`). The panel is not copied: the Gradle build runs the extension's own `npm run build` in `../vscode` and packs `out/webview.js`, `out/webview.css` and the logo into the plugin as resources (`buildWebview` and `syncWebview` tasks). So the build needs Node.js 22+ and npm; it runs `npm ci` there when `node_modules/` is missing.

- The page talks to the plugin through one `JBCefJSQuery`. `frontends/ide/vscode/webview/host.ts` is the only place in the panel that knows which IDE it runs in.
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
- New requests wait for **two seconds without typing**. Further typing cancels the in-flight request and its process. Cache lookup happens before the pause: deleting a typo or undoing restores previous alternatives immediately. Matching forward input consumes the suggestion; unrelated text hides it.
- One fast-model request prepares up to three alternatives, exposed through the IDE's inline-completion variants. Reasoning is disabled where supported, with minimal/low effort as a fallback. This is independent of chat's reasoning setting.
- A bounded history includes workspace, binary, language and full buffer context, so another project or changed surrounding code cannot reuse an unrelated suggestion.
- `prefix`/`suffix` are the live, unsaved buffer split at the caret — never a save to disk first.
- Only Python and JS/TS variants get their imports resolved into extra context by the core; every other language still gets a plain completion.
- The bundled binary must be built from a Kvist version that has `--complete`; rebuild it with the scripts below.

## Editor context menu

Right-click in the editor: the **Kvist** submenu (first entry of the menu) has three actions that work on the selected text. They are disabled without a selection, and in read-only viewers.

- **Rewrite as Senior**: asks the model for a cleaner version of the selected fragment (same behavior, simpler conditions, less duplication, better local names, no API changes, no new architecture).
- **Translate Selection**: pick a language (English, Russian, Kazakh, Kyrgyz, Arabic, British English, American English, or a custom one); comment markers, string quotes and escapes, Markdown markup and HTML/XML tags are kept.
- **Explain and Fix**: a dialog with the explanation, risks, bugs and, when a safe patch exists, why it is safe. Logic changes, public API changes, edits in other files, renames, broad refactorings and dependency changes are not proposed as patches.

Nothing is written without a confirmation: every replacement (and **Apply Patch…** in Explain and Fix) first opens a diff of the selection against the suggestion, and only **Apply** edits the document, in one write command, so Undo reverts it in a single step. If the document changed while the model was answering, the edit is refused ("Document changed, retry action").

- One short-lived `kvist --selection-task` process per request, using the provider and **fast** model saved in `~/.kvist/config.json`. The prompts are built by the plugin (`selection/prompt/`); the selected code is quoted in `<UNTRUSTED_TOOL_OUTPUT>` blocks, and the core path has no tools, so the model can only return text.
- Besides the selection, about 20 lines of code on each side (at most 2000 characters) are sent as context.
- The bundled binary must be built from a Kvist version that has `--selection-task`; rebuild it with the scripts below.

## Tests

```bash
./gradlew test
```

Platform tests (editor, undo, action registration) run headless. The first run downloads the IntelliJ test framework and JUnit.

## Compatibility

The plugin is built against IntelliJ Platform 2025.3 (build 253), its minimum supported version because it uses the reworked Terminal API. The minimum version stays at 253; newer platform versions, including 2026.2, are allowed.

In 2026.2, JCEF moved from the platform into the bundled **Web Browser (JCEF)** plugin. Kvist declares an optional dependency on `com.intellij.modules.jcef` for classloader access on these IDEs. The dependency is optional so the original 2025.3 builds, which have JCEF in the platform but no plugin alias, still load the chat window. If JCEF is absent, disabled or unsupported by the boot runtime, the window offers **Open Kvist in Terminal** instead.

## Supported platforms

- Windows
- Linux
- macOS (best-effort)

macOS (Apple Silicon and Intel) is best-effort: the plugin ships a binary for each, but nobody has verified it on a Mac, so there is no guarantee that every feature works there.

## Binaries

Put the real binaries here before running packaging or sandbox tasks:

- bin/win/kvist.exe
- bin/linux/kvist.bin
- bin/mac-arm64/kvist.bin (optional)
- bin/mac-x64/kvist.bin (optional)

If the binary for the host you build on is missing, the build fails with a clear error. The other platforms' binaries are optional: a missing one only produces a warning, and the plugin is built without support for that platform.

The helper scripts below rebuild only the binary for the current host OS:

- Linux host -> refreshes `bin/linux/kvist.bin`
- Windows host -> refreshes `bin/win/kvist.exe`
- macOS host -> refreshes `bin/mac-arm64/kvist.bin` (Apple Silicon) or `bin/mac-x64/kvist.bin` (Intel)

So for a full distributable ZIP, keep the binary for the other OS in `bin/` as well.

## Build

Use JDK 21.

You also need Python with project dependencies installed and `PyInstaller` available in that environment.

## Quick build with OS-specific scripts

These scripts do the fast path for local packaging:

1. build the Kvist binary for the current OS with `PyInstaller`
2. copy it into `frontends/ide/jetbrains/bin/...`
3. refresh the IntelliJ sandbox copy if it already exists
4. run the Gradle plugin build

Run them from the `frontends/ide/jetbrains/` directory.

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
- run the PyInstaller step from the repository root, not from `frontends/ide/jetbrains/`

### Manual build on Linux

1. From the repository root, build the Linux executable with PyInstaller:

```bash
python -m PyInstaller kvist.spec
```

2. Copy the result into the plugin directory:

```bash
install -Dm755 dist/kvist frontends/ide/jetbrains/bin/linux/kvist.bin
```

3. Make sure the Windows binary is already present:

- `frontends/ide/jetbrains/bin/win/kvist.exe`

4. Build the plugin ZIP:

```bash
cd frontends/ide/jetbrains
./gradlew clean buildPlugin
```

Result:

- `frontends/ide/jetbrains/build/distributions/*.zip`

### Manual build on Windows

1. From the repository root, build the Windows executable with PyInstaller:

```powershell
python -m PyInstaller kvist.spec
```

2. Copy the result into the plugin directory:

```powershell
Copy-Item dist\kvist.exe frontends\ide\jetbrains\bin\win\kvist.exe -Force
```

3. Make sure the Linux binary is already present:

- `frontends/ide/jetbrains/bin/linux/kvist.bin`

4. Build the plugin ZIP:

```powershell
cd frontends/ide/jetbrains
.\gradlew.bat clean buildPlugin
```

Result:

- `frontends\ide\jetbrains\build\distributions\*.zip`

## Typical workflows

### Fast local Linux build

1. Make sure `bin/win/kvist.exe` is already present.
2. Run `./build-plugin.sh`.
3. Pick the ZIP from `build/distributions/`.

### Fast local macOS build

1. Run `./build-plugin.sh` (the Linux script; it picks the folder by `uname -m`). It needs JDK 21 (`JAVA_HOME`, Homebrew `openjdk@21` or `/Library/Java/JavaVirtualMachines` are searched) and Python with PyInstaller.
2. Pick the ZIP from `build/distributions/`.
3. Copy the other platforms' binaries into `bin/` first if the ZIP should carry them.

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
- macOS (best-effort)

macOS is best-effort: the plugin ships binaries for Apple Silicon and Intel, but they are not verified on a Mac.