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

## Build

Use JDK 21.

```bash
./gradlew buildPlugin
```

The plugin ZIP is created in build/distributions/.

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