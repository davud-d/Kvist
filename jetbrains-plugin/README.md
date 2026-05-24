# DevAgent IntelliJ Plugin

Minimal IntelliJ Platform plugin that starts the DevAgent binary directly inside the IDE Terminal for the currently opened project.

## Compatibility

This MVP is intentionally pinned to IntelliJ Platform 2025.3.

It uses the reworked Terminal API, which is the reason the plugin is limited to the 253 platform line.

## Supported platforms

- Windows
- Linux

macOS is intentionally unsupported in this MVP.

## Binaries

Put the real binaries here before running packaging or sandbox tasks:

- bin/win/devagent.exe
- bin/linux/devagent.bin

If any required binary is missing, the build fails with a clear error.

The plugin ZIP always expects both files to exist.

The helper scripts below rebuild only the binary for the current host OS:

- Linux host -> refreshes `bin/linux/devagent.bin`
- Windows host -> refreshes `bin/win/devagent.exe`

So for a full distributable ZIP, keep the binary for the other OS in `bin/` as well.

## Build

Use JDK 21.

You also need Python with project dependencies installed and `PyInstaller` available in that environment.

## Quick build with OS-specific scripts

These scripts do the fast path for local packaging:

1. build the DevAgent binary for the current OS with `PyInstaller`
2. copy it into `jetbrains-plugin/bin/...`
3. refresh the IntelliJ sandbox copy if it already exists
4. run the Gradle plugin build

Run them from the `jetbrains-plugin/` directory.

### Linux

```bash
./build-plugin.sh
```

What it updates:

- `bin/linux/devagent.bin`
- `build/distributions/*.zip`

### Windows (PowerShell)

```powershell
.\build-plugin.ps1
```

What it updates:

- `bin/win/devagent.exe`
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

This Gradle-only path does not rebuild the DevAgent executable. It just packages whatever is already present in `bin/win/` and `bin/linux/`.

## Manual build by operating system

Use this path if you do not want to use the helper scripts and prefer running each step yourself.

Important:

- build the executable on the target OS itself
- keep the binary for the other supported OS in `bin/` as well if you want a distributable ZIP
- run the PyInstaller step from the repository root, not from `jetbrains-plugin/`

### Manual build on Linux

1. From the repository root, build the Linux executable with PyInstaller:

```bash
python -m PyInstaller devagent.spec
```

2. Copy the result into the plugin directory:

```bash
install -Dm755 dist/devagent jetbrains-plugin/bin/linux/devagent.bin
```

3. Make sure the Windows binary is already present:

- `jetbrains-plugin/bin/win/devagent.exe`

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
python -m PyInstaller devagent.spec
```

2. Copy the result into the plugin directory:

```powershell
Copy-Item dist\devagent.exe jetbrains-plugin\bin\win\devagent.exe -Force
```

3. Make sure the Linux binary is already present:

- `jetbrains-plugin/bin/linux/devagent.bin`

4. Build the plugin ZIP:

```powershell
cd jetbrains-plugin
.\gradlew.bat clean buildPlugin
```

Result:

- `jetbrains-plugin\build\distributions\*.zip`

## Typical workflows

### Fast local Linux build

1. Make sure `bin/win/devagent.exe` is already present.
2. Run `./build-plugin.sh`.
3. Pick the ZIP from `build/distributions/`.

### Fast local Windows build

1. Make sure `bin/linux/devagent.bin` is already present.
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
5. Run DevAgent from the toolbar or Tools menu.
6. Confirm that the IDE Terminal starts:

```text
devagent --workspace "<project_path>"
```

## Supported operating systems

- Windows
- Linux

macOS is intentionally unsupported.