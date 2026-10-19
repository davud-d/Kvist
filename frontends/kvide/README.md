# kvide commit-message generator

[kvide](../../../kvide) lists changed files in its commit panel and runs the
generators named in a TOML file passed with `--generators`. `kvist_generator.py`
is that generator for Kvist: it hands the selected changes to
`kvist-core --commit-message` — the command the JetBrains commit button uses —
and puts the answer into the message field. The provider, the fast model, the
protected-path filter and the token budget are the core's; the adapter only
translates between the two JSON payloads, so it needs nothing but the standard
library.

```bash
python scripts/installer/install_cli.py     # puts kvist-core in place
python scripts/installer/install_kvide.py   # or: install.py --kvide
kvide                                       # the Kvist button is there
```

The installer writes kvide's user file (`$XDG_CONFIG_HOME/kvide/generators.toml`,
else `~/.config/kvide/`; `%APPDATA%\kvide` on Windows), which kvide reads when
started without `--generators`. A user file Kvist did not write (it starts with
`# managed by kvist`) is never overwritten or removed; the installer then keeps
a copy at `~/.local/share/kvist/kvide/generators.toml` and prints the
`kvide --generators` command for it.

Open the commit panel (`Ctrl+G`), select files, press `Kvist` or `Ctrl+F1`.
Generation never commits. `install_kvide.py --uninstall` removes the
`kvide/` directory next to the binary and the user file if Kvist wrote it.

What the adapter does with kvide's input (`{"version", "files", "diff"}`):

- splits `diff` into one patch per file (`diff --git` sections and kvide's
  `New file: <path>` blocks), because the core drops secret and `.kvist/` paths
  per file;
- adds the last 15 commit subjects from `git log` so the message matches the
  repository's style;
- turns the core's `{"ok": false, "error": ...}` into kvide's
  `{"error": {"code", "message"}}` envelope.

Tests: `frontends/kvide/tests`, `scripts/installer/tests/test_install_kvide.py`.

## Autocomplete

`completion-extension/` is a kvide inline-completion extension. It runs
`kvist-core --complete-server` (the process the IDE plugins use) and shows one
suggestion as ghost text after the cursor, like VS Code: Tab accepts, Esc
dismisses, typing the same characters shortens it. Only the first line is shown
and inserted; the next pause suggests the following one.

```bash
python scripts/installer/install_kvide.py   # also copies the extension and records the core
kvide --completion-extension ~/.local/share/kvist/kvide/completion-extension
```

kvide has no user file for extensions, and the extension is JavaScript with your
privileges, so it is enabled only by that flag. `KVIST_CORE` overrides the
recorded core path. The first suggestion waits for the core to start
(about 1.5 s) and the model; a reply that takes over 12 s is dropped without a message. Tests: `frontends/kvide/tests`.
