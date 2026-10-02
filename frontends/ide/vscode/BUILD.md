# Building the Kvist VS Code Extension

## Prerequisites

- Node.js 18+ and npm
- Python with the Kvist project dependencies installed
- PyInstaller available in that Python environment
- `@vscode/vsce` from `npm install` / `npm ci`

## Quick Build with OS-specific Scripts

The scripts below follow the same layout as the JetBrains plugin build scripts:
the Python core is built into the extension's private `.build/core` directory,
then copied into `bin/`, and finally the VS Code extension is compiled and
packaged.

Run them from the `frontends/ide/vscode/` directory.

### Linux

```bash
./build-plugin.sh
```

What it updates:

- `bin/linux/kvist.bin`
- `.build/vsix/kvist-*-linux-x64.vsix`

### macOS (best-effort)

```bash
./build-plugin.sh
```

The script is the Linux one; it picks the folder by `uname -m`.

What it updates:

- `bin/mac-arm64/kvist.bin` (Apple Silicon) or `bin/mac-x64/kvist.bin` (Intel)
- `.build/vsix/kvist-*-macos-arm64.vsix` or `kvist-*-macos-x64.vsix`

### Windows PowerShell

```powershell
.\build-plugin.ps1
```

What it updates:

- `bin\win\kvist.exe`
- `.build\vsix\kvist-*-win32-x64.vsix`

### Windows Command Prompt

```bat
build-plugin.bat
```

Use this if you prefer plain `cmd.exe` instead of PowerShell.

The helper scripts rebuild only the binary for the current host. If you want one
package to work on several platforms, keep the binaries for the others in `bin/` as well.

## Manual Build Steps

### 1. Install Dependencies

```bash
cd frontends/ide/vscode
npm install
```

### 2. Compile TypeScript

```bash
npm run compile
```

Or watch for changes:

```bash
npm run watch
```

### 3. Bundle Kvist Binaries

Before packaging, you need to place the Kvist binaries in the correct locations:

- **Windows**: Copy `kvist.exe` to `bin/win/kvist.exe`
- **Linux**: Copy `kvist` binary to `bin/linux/kvist.bin`
- **macOS**: Copy `kvist` binary to `bin/mac-arm64/kvist.bin` (Apple Silicon) or `bin/mac-x64/kvist.bin` (Intel)

To build the Kvist binaries from the main project:

```bash
cd ..  # Go back to main Kvist directory
.venv/bin/python -m PyInstaller kvist.spec

# Copy the built binaries
cp dist/kvist frontends/ide/vscode/bin/linux/kvist.bin
# For Windows or macOS, build on that system and copy to bin/win/kvist.exe or bin/mac-<arch>/kvist.bin
```

### 4. Package the Extension

```bash
npm run package
```

This creates a `.vsix` file that can be installed in VS Code. The manual npm
script writes it to the extension directory; the helper scripts write their
OS-specific packages under `.build/vsix/`.

### 5. Install the Extension

In VS Code:
1. Open the Extensions view (Ctrl+Shift+X)
2. Click the `...` menu at the top
3. Select "Install from VSIX..."
4. Choose the generated `.vsix` file

Or from command line:

```bash
code --install-extension kvist-0.1.0.vsix
```

## Development

To test during development:

1. Open the `frontends/ide/vscode` folder in VS Code
2. Press F5 to launch an Extension Development Host
3. Test your commands in the new VS Code window

## Linting

```bash
npm run lint
```

## Directory Structure

```
frontends/ide/vscode/
├── src/
│   ├── extension.ts          # Main entry point
│   ├── launcher.ts            # Kvist terminal launcher
│   ├── commitMessage.ts       # Commit message generator
│   └── binaryResolver.ts      # Binary path resolution
├── build-plugin.sh            # Linux helper build
├── build-plugin.ps1           # Windows PowerShell helper build
├── build-plugin.bat           # Windows cmd.exe wrapper
├── bin/
│   ├── win/                   # Windows binaries
│   └── linux/                 # Linux binaries
├── .build/                    # Private PyInstaller and VSIX output
├── resources/                 # Icons and assets
├── out/                       # Compiled JavaScript (generated)
├── package.json               # Extension manifest
└── tsconfig.json              # TypeScript config
```

## Publishing (Future)

To publish to the VS Code Marketplace:

1. Create a Personal Access Token at https://dev.azure.com
2. Create a publisher: `vsce create-publisher <publisher-name>`
3. Login: `vsce login <publisher-name>`
4. Publish: `vsce publish`

For now, distribute the `.vsix` file directly.
