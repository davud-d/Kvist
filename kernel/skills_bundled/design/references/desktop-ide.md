# Desktop apps, IDE panels and native conventions

Desktop software lives among other software. People judge it by how well it
behaves as a citizen of their OS and their editor: does it remember where its
window was, use the right shortcuts, follow the system theme, close when
asked. Most of this is convention, and convention is what makes a new tool
feel familiar on the first day.

## Platform conventions

| | macOS | Windows | Linux (GNOME / KDE) |
| --- | --- | --- | --- |
| Primary modifier | ⌘ Cmd | Ctrl | Ctrl |
| Settings | App menu → Settings… (⌘,) | File/Tools → Options or a gear | Hamburger/app menu → Preferences |
| Quit | ⌘Q quits; closing the last window often keeps the app | Closing the main window quits (unless it lives in the tray) | Closing the main window quits |
| Dialog buttons | primary on the right | OK before Cancel, right-aligned | differs by toolkit and desktop: use the toolkit's standard button box (`QDialogButtonBox`, GTK dialog responses) and let it order them |
| Menu bar | global, at the top of the screen | in the window | in the window, or a header bar (GNOME) |
| Window controls | left (traffic lights) | right | right by default, configurable |
| Shortcut symbols | ⌘ ⌥ ⇧ ⌃ | Ctrl+Alt+Shift | Ctrl+Alt+Shift |

- Show shortcuts with the platform's symbols and modifier names.
- Follow the system theme (light, dark, high contrast) and accent colour
  where the platform exposes them; offer an override.
- Respect system settings: reduced motion, reduced transparency, text size,
  24-hour clock, first day of the week, locale.
- High-DPI: vector icons or 1×/2×/3× assets; test at 100 %, 150 % and 200 %
  scaling on Windows and fractional scaling on Linux.
- The window remembers its size, position and monitor; if that monitor is
  gone, it opens on the current one, fully visible.
- Single instance where it makes sense: opening the app again focuses the
  existing window (and passes along the file or link).
- Startup: show a window with real structure fast (under about a second),
  then fill it; a splash screen is a last resort.

## Electron and other web-based desktop apps

- Use native dialogs (`dialog.showMessageBox`, `showOpenDialog`) or in-page
  modal components for confirmation. `window.confirm`, `alert` and `prompt`
  are unreliable in Electron (they block, look foreign, and under some
  Wayland setups silently do nothing).
- Native menus with roles (`role: "copy"`, `"undo"`, `"quit"`) so standard
  shortcuts and the macOS app menu work.
- `nativeTheme` for the system theme; `titleBarStyle` and
  `titleBarOverlay` if you draw a custom title bar, and then keep the window
  draggable (`-webkit-app-region: drag`) and the controls where the platform
  puts them.
- Links to the web open in the default browser (`shell.openExternal`), never
  inside the app window, and only for `https:` URLs you expect.
- Security is part of UX: `contextIsolation`, no `nodeIntegration` in
  renderers, a strict CSP, IPC with validated messages. A compromised app is
  the worst experience.
- Auto-update quietly in the background, apply on restart, say what changed
  once; never force-restart mid-work.
- Offline: the app opens and shows local data without a network.
- System notifications for long work that finished while the window was
  hidden; the click on one brings the right window and item into view.

## Tray and status-bar icons

- Encode state in the icon's shape as well as its colour (a dot, a badge, a
  different glyph), because tray icons are tiny and often monochrome.
- Tray support differs: Windows and KDE show tray icons; GNOME needs an
  extension (AppIndicator); some tiling setups use their own bars (Waybar,
  Polybar). Provide another way to reach every function (the main window, a
  command), and detect rather than assume.
- A click opens what people expect on that platform (a menu on macOS and
  Linux indicators; on Windows a left click often opens the window and a right
  click the menu).
- If the app edits the person's bar or shell configuration, do it once,
  idempotently, keep a backup, and remove exactly what was added on uninstall.
- Never spawn more than one background process for the tray; guard it with a
  lock and exit when nobody needs it.

## VS Code extensions

- Prefer native UI over webviews: tree views, the quick pick, input boxes,
  the status bar, notifications, the problems panel, decorations, CodeLens.
  They follow the person's theme, keyboard and accessibility settings for
  free. Use a webview only for what native views cannot show (a chat panel, a
  rich preview).
- In webviews, use the theme's CSS variables (`--vscode-foreground`,
  `--vscode-editor-background`, `--vscode-button-background`,
  `--vscode-focusBorder`, `--vscode-font-family`, `--vscode-font-size`,
  `--vscode-editor-font-family`) instead of your own palette, and test the
  light, dark, high-contrast dark and high-contrast light theme kinds (the
  body gets `vscode-light`, `vscode-dark`, `vscode-high-contrast`,
  `vscode-high-contrast-light` classes). Use codicons for icons.
- Keep the webview's state with `getState`/`setState` so a hidden and shown
  panel comes back as it was; `retainContextWhenHidden` costs memory, use it
  only when rebuilding is expensive.
- A strict Content Security Policy in the webview; load resources through
  `asWebviewUri`.
- Commands: each one in the command palette with a category prefix
  ("Kvist: Generate commit message"), `when` clauses so they appear only
  where they apply, keybindings that do not collide with defaults.
- Status bar items: short text, an icon, a tooltip, a command on click; one
  or two items at most.
- Notifications sparingly: errors the person must act on, with buttons for
  the action; progress for long work with `withProgress` (in the place the
  work concerns: a view, the source control panel, or a notification).
- First run: a walkthrough (`contributes.walkthroughs`) with a few steps that
  open the real commands, rather than a welcome webview that pops up on every
  start. Never open a tab on startup after the first time.
- Settings in `contributes.configuration` with descriptions, defaults and
  enums with `enumDescriptions`, so the settings UI explains them.

## JetBrains plugins

- Follow the IntelliJ Platform UI Guidelines: platform components (Kotlin UI
  DSL for forms, `JBList`, `JBTable`, `SearchTextField`, tool windows,
  `Messages` dialogs, balloon notifications) instead of raw Swing.
- Colours through `JBColor` (light and dark pairs) and the theme's UI keys;
  sizes through `JBUI.scale` so HiDPI works.
- Tool windows for persistent panels, with an icon that is a 13×13 or 16×16
  monochrome SVG plus a `_dark` variant.
- Actions registered so they appear in Find Action (Ctrl/Cmd+Shift+A) with a
  clear text and description; place them in the existing menus and toolbars
  rather than creating new ones.
- Settings in Settings → Tools (a `Configurable`), searchable.
- Long work as background tasks with a progress indicator and cancel; never
  block the UI thread (the IDE freezes and reports your plugin).
- A web UI in JCEF should still follow the IDE theme (pass its colours into
  the page) and fall back to native UI when JCEF is unavailable.

## Zed and other editors

- Respect the editor's own model: Zed integrates agents through the Agent
  Client Protocol and its own panels; do not imitate UI the editor does not
  offer. Check the editor's current documentation before designing for it;
  extension APIs change.

## Checking desktop and IDE UI

- Light, dark and high-contrast themes; 100 % and 200 % scaling.
- Keyboard only: every command reachable, focus visible, standard shortcuts
  work.
- First launch on a clean profile (a fresh VS Code user-data directory, a new
  IDE sandbox, a new OS user): what opens, what is asked, what is shown.
- Quit and reopen: window position, panel state and settings are kept.
- Disconnect the network: the app opens and says what is unavailable.
