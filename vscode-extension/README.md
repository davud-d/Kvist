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
`jetbrains-plugin/.../chat/KvistChatHost.kt`, which answers the same messages.

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

## Requirements

- VS Code 1.85 or newer
- Node.js 18+ and npm for building the extension
- Python with the Kvist project dependencies and PyInstaller available when using the helper build scripts
- Git must be initialized in your workspace for commit-message generation
- Configure Kvist once in a terminal first; the extension uses `~/.kvist/config.json` and never prompts for API keys

## Supported Platforms

- Windows (x64)
- Linux (x64)

macOS is not supported in this MVP.

## Quick Build

The helper scripts mirror the JetBrains plugin flow: they build the Kvist binary
for the current host OS, copy it into the extension's `bin/` directory, compile
TypeScript, and package a VSIX.

### Linux

```bash
cd vscode-extension
./build-plugin.sh
```

What it updates:

- `bin/linux/kvist.bin`
- `.build/vsix/kvist-*-linux-x64.vsix`

### Windows PowerShell

```powershell
cd vscode-extension
.\build-plugin.ps1
```

What it updates:

- `bin\win\kvist.exe`
- `.build\vsix\kvist-*-win32-x64.vsix`

### Windows Command Prompt

```bat
cd vscode-extension
build-plugin.bat
```

Use this if you prefer plain `cmd.exe` instead of PowerShell.

The scripts build only the current OS binary. For a VSIX that should work on
both Linux and Windows, keep the other platform's binary in `bin/` as well.

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
