# Installing and first launch

## Install

- **Release script** (no Python, no administrator rights): on Linux
  `curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.sh | bash`;
  on Windows `irm https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.ps1 | iex`.
  In a terminal it opens a screen with tick boxes (the `kvist` command, Node.js for the
  terminal interface, ffmpeg for demo clips, the JetBrains plugin, the VS Code extension,
  PATH). Nothing is written until the person presses Install; downloads are checked
  against SHA-256 sums. Without a terminal: `KVIST_INSTALL_PLAIN=1 bash install.sh`.
- **Single release assets**: `kvist-linux-x64`, `kvist-windows-x64.exe`, `kvist-macos-arm64`,
  `kvist-macos-x64`, `kvist-jetbrains.zip`, `kvist-vscode.vsix`. From files already on
  disk: `bash install.sh --from <dir>`.
- **From source** (Python 3.11+): clone the repository, create `.venv`, install
  `requirements.txt`, optionally run `scripts/installer/install.py` to put `kvist` on PATH
  and choose parts (CLI, JetBrains, VS Code, desktop app, Zed).

## First launch

```
cd /path/to/project
kvist
```

- Without `--workspace`, Kvist works in the current directory. In the home directory or a
  filesystem root it opens its system folder `~/.kvist/workspace` (beside the config that
  `KVIST_CONFIG_DIR` names, when set): for requests about the computer and everyday things,
  not a project. `KVIST.md` there is a notebook on the computer; see safety.md for sudo.
- A clean machine shows a setup screen: welcome, provider, API key, (model for providers
  without defaults), interface, and an import step if sessions of other agents were found.
  Esc goes back; Esc on the first screen or Ctrl+C quits with nothing saved. Without Node.js the plain
  terminal wizard asks the same questions.
- The key variable of a provider (for example `OPENAI_API_KEY`) together with `AGENT_LLM_PROVIDER`
  skips the questions.
- On the first launch Kvist creates `KVIST.md` and `KVIST-CHAT.md` in the workspace
  (project instructions; see the `safety` page).
- `kvist --setup` opens the setup screen again; `/full-reset` wipes saved providers and keys.

## Platforms

- **Linux**: fully supported, including the Bubblewrap sandbox and executable skills.
- **Windows**: supported without Bubblewrap; there is nothing that can cut a command off the
  network, so Kvist says so at start and in every approval. Executable skills do not run.
- **macOS**: checked in CI on Apple Silicon; no guarantee for every feature; executable
  skills do not run.
- Python 3.11+ only matters when running from source.

## Upgrading

Run the release script again. Bundled skills move to the version a new Kvist ships; a
skill the person uninstalled stays removed.
