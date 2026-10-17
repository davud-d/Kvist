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
kvide --generators ~/.local/share/kvist/kvide/generators.toml
```

Open the commit panel (`Ctrl+G`), select files, press `Kvist` or `Ctrl+F1`.
Generation never commits. `install_kvide.py --uninstall` removes the
`kvide/` directory next to the binary and nothing else.

What the adapter does with kvide's input (`{"version", "files", "diff"}`):

- splits `diff` into one patch per file (`diff --git` sections and kvide's
  `New file: <path>` blocks), because the core drops secret and `.kvist/` paths
  per file;
- adds the last 15 commit subjects from `git log` so the message matches the
  repository's style;
- turns the core's `{"ok": false, "error": ...}` into kvide's
  `{"error": {"code", "message"}}` envelope.

Tests: `frontends/kvide/tests`, `scripts/installer/tests/test_install_kvide.py`.
