# Kvist VS Code Extension

Launch the Kvist coding agent directly in VS Code and generate commit messages with AI.

## Features

- **Launch Kvist**: Open Kvist in a VS Code terminal with one command
- **Generate Commit Messages**: AI-powered commit message generation based on staged changes

## The chat panel in JetBrains IDEs

The chat panel's webview (`webview/`) is also the JetBrains plugin's chat tool
window: the plugin's Gradle build runs `npm run build` here and packs
`out/webview.js` and `out/webview.css`. `webview/host.ts` is the only file that
knows the host: it calls `acquireVsCodeApi` in VS Code and the `window.kvistHost`
bridge the JetBrains plugin injects. Keep IDE-specific code out of the other
webview files, and keep `src/messages.ts` in step with
`frontends/ide/jetbrains/.../chat/KvistChatHost.kt`, which answers the same messages.

## Usage

### Launch Kvist

1. Open a workspace folder
2. Open the Command Palette (Ctrl+Shift+P / Cmd+Shift+P)
3. Run `Kvist: Launch Kvist`

The agent will start in a new terminal window.

### Generate Commit Message

1. Stage your changes in Git
2. Click the sparkle icon in the Source Control panel, or
3. Open the Command Palette and run `Kvist: Generate Commit Message with Kvist`

The generated message will be inserted into the commit message input box.

## Inline completion

Kvist uses your configured fast model and requests disabled reasoning where the
provider supports it, falling back to minimal/low effort when necessary. One
request prepares up to three distinct suggestions; use VS Code's inline suggestion
next/previous commands to cycle them.

Automatic requests wait for **two seconds without typing** by default
(`kvist.inlineCompletion.debounceMs`). A manual inline-suggestion request skips
the pause. Matching input consumes the existing suggestion. A typo hides it;
deleting the typo or undoing restores cached alternatives immediately. History
is bounded and scoped to the workspace, file, language and buffer context.
Superseded requests are cancelled and late results are discarded.

The updated core returns `completions` plus the legacy first `completion`.
Rebuild the bundled core to enable multiple alternatives; an older core still
works with one suggestion. Toggle suggestions with `kvist.inlineCompletion.enabled`.

## Requirements

- VS Code 1.85 or newer
- Node.js 18+ and npm for building the extension
- Python with the Kvist project dependencies and PyInstaller available when using the helper build scripts
- Git must be initialized in your workspace for commit-message generation
- Configure Kvist once in a terminal first; the extension uses `~/.kvist/config.json` and never prompts for API keys

## Supported Platforms

- Windows (x64)
- Linux (x64)
- macOS (Apple Silicon and Intel), best-effort: the extension ships a binary for each, but
  nobody has verified it on a Mac, so there is no guarantee that every feature works there.

## Quick Build

The helper scripts mirror the JetBrains plugin flow: they build the Kvist binary
for the current host OS, copy it into the extension's `bin/` directory, compile
TypeScript, and package a VSIX.

### Linux

```bash
cd frontends/ide/vscode
./build-plugin.sh
```

What it updates:

- `bin/linux/kvist.bin`
- `.build/vsix/kvist-*-linux-x64.vsix`

### macOS

```bash
cd frontends/ide/vscode
./build-plugin.sh
```

The same script as on Linux. It needs Python 3 with PyInstaller (the repository's `.venv`
is used when it exists) and Node.js. What it updates:

- `bin/mac-arm64/kvist.bin` on Apple Silicon, `bin/mac-x64/kvist.bin` on Intel
- `.build/vsix/kvist-*-macos-arm64.vsix` (`macos-x64` on Intel)

The binary is not notarized. The extension clears the quarantine mark before it starts
the binary, but a copy downloaded by a browser may need "Open" from the context menu once.

### Windows PowerShell

```powershell
cd frontends/ide/vscode
.\build-plugin.ps1
```

What it updates:

- `bin\win\kvist.exe`
- `.build\vsix\kvist-*-win32-x64.vsix`

### Windows Command Prompt

```bat
cd frontends/ide/vscode
build-plugin.bat
```

Use this if you prefer plain `cmd.exe` instead of PowerShell.

The scripts build only the current host's binary. For a VSIX that should work on
several platforms, keep their binaries in `bin/` as well; the release build does that
(`.github/workflows/plugin-release.yml`).

Install the generated package from VS Code with Extensions > Install from VSIX,
or from the command line:

```bash
code --install-extension .build/vsix/kvist-0.1.0-linux-x64.vsix
```

## Extension Settings

This extension does not add any VS Code settings. Configuration is managed by Kvist itself through `~/.kvist/config.json`.

## Manual Build

Use this path when you do not want the helper script to rebuild the bundled
binary.

```bash
npm install
npm run compile
```

Before packaging, place the Kvist binary in the expected location:

- Windows: `bin/win/kvist.exe`
- Linux: `bin/linux/kvist.bin`
- macOS: `bin/mac-arm64/kvist.bin` (Apple Silicon) and `bin/mac-x64/kvist.bin` (Intel)

Then package the extension:

```bash
npm run package
```

The `npm run package` path writes `kvist-0.1.0.vsix` in the extension directory.
The OS-specific helper scripts write their packages under `.build/vsix/`.

## Known Issues

None yet.

## Release Notes

### 0.1.0

Initial release:
- Launch Kvist in VS Code terminal
- Generate commit messages from staged changes
