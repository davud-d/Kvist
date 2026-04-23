# DevAgent IntelliJ Plugin

Minimal IntelliJ Platform plugin that starts the DevAgent binary directly inside the IDE Terminal for the currently opened project.

## Supported platforms

- Windows
- Linux

macOS is intentionally unsupported in this MVP.

## Binary placement

Put the real binaries here before running sandbox or packaging tasks:

- bin/win/devagent.exe
- bin/linux/devagent.bin

The build fails fast if any of these files is missing.

## Build

Use JDK 21 and Gradle 8.13+.

```bash
gradle buildPlugin
```

## Run sandbox IDE

Use JDK 21 and Gradle 8.13+.

```bash
gradle runIde
```

## MVP verification

1. Put the real binaries into the bin directory.
2. Start the sandbox IDE with gradle runIde.
3. Open any project in the sandbox IDE.
4. Click Run DevAgent in the main toolbar or use Tools | Run DevAgent.
5. Confirm that the Terminal opens in the current project directory.
6. Confirm that the started process is the platform binary with --workspace <project_path>.