# Kvist in your own browser (`kvist --browser-mcp`)

[Русская версия](browser-mcp.ru.md)

A local MCP server that lets Kvist work in the browser you already use: your tabs, your logins.

* **Firefox family** (Firefox, Waterfox, LibreWolf): the **Kvist bridge**, a small script the browser
  loads at start-up. No remote debugging, so sites keep seeing an ordinary browser.
* **Chromium family** (Chrome, Chromium, Brave, Edge, Vivaldi, Yandex): the browser's debugging
  port and the Chrome DevTools Protocol (with it Chromium still reports `navigator.webdriver = false`).

While Kvist works in a tab, the page shows a glowing blue frame, a "Kvist is working here" pill
with the current step and a **Stop** button, and a blue pointer labelled *Kvist*. The pointer is
not a picture laid over the page: Kvist moves the browser's own pointer along the same path, so
the site receives ordinary trusted mouse and keyboard events (hover, press, release, wheel,
key by key typing). Pressing **Stop** takes the browser back for the rest of that turn: Kvist is told
not to get around it and to ask what went wrong and whether it may go on; your next message opens
the browser to it again.

## 1. Connect the browser

### Firefox, Waterfox, LibreWolf: the Kvist bridge

```bash
kvist --browser-bridge install     # copies the bridge to ~/.kvist/browser-bridge, prints the next step
```

Once, as administrator, copy the loader it names into the browser's install folder (for Waterfox
in `/opt/waterfox`), then restart the browser fully:

```bash
sudo cp ~/.kvist/browser-bridge/kvist-autoconfig.cfg /opt/waterfox/
sudo cp ~/.kvist/browser-bridge/autoconfig.js /opt/waterfox/defaults/pref/
```

`kvist --browser-bridge status` shows what is in place. Later versions of the bridge need only
`install` and a browser restart. To remove it, delete those two files from the browser's folder.

How it works: the loader is Firefox's own autoconfig, which runs with the browser's privileges.
The bridge listens on a Unix socket only your user can open (`$XDG_RUNTIME_DIR/kvist-browser-bridge.sock`),
lists the tabs from the tab strip (unloaded ones too, without loading them) and runs Kvist's page
helpers in a sandbox of each page. Mouse and wheel input is dispatched in the page's process and
keys at the browser window, the way real devices do; pages receive trusted events. The browser's
own pages (settings, add-ons, `about:`) are never touched.

Why not remote debugging: while Firefox runs with `--remote-debugging-port`, it tells every page
`navigator.webdriver = true`, whether Kvist is working or not, and Cloudflare, YouTube and others
then treat you as a robot (captchas, missing features). The port also breaks on tabs restored but
never opened (Firefox 140). If you use the port anyway, `kvist --browser-relay` holds its single
session and the loader patches the restored-tab fault; see the source of `relay.py`.

### Chrome, Brave, Edge, Chromium

Open `chrome://inspect/#remote-debugging` and turn it on (recent versions; your normal profile),
**or** start with `--remote-debugging-port=9222` (Chrome 136+ honours the flag only with a
separate `--user-data-dir`). The server finds the browser by its `DevToolsActivePort` file, then
tries port 9222; desktop apps on Chromium that hold 9222 (Spotify, Electron apps) are recognised
and skipped. Another port goes in `--browser-endpoint 9333`.

Remote debugging gives full control of the browser to any local program that connects to the
port. Turn it off when you do not need it.

## 2. Register the server

```bash
kvist --mcp-op add --mcp-name browser --mcp-payload "{
  \"command\": [\"$(command -v kvist)\", \"--browser-mcp\"],
  \"sandbox\": \"none\", \"network\": true,
  \"readonly_tools\": [\"browser_tabs\", \"browser_snapshot\", \"browser_page_text\", \"browser_screenshot\"],
  \"description\": \"The person's own browser: tabs, logins, clicks and typing\"}"
```

`sandbox` must be `none`: the server has to reach the bridge socket, or the browser's local port and its profile folder.
Add tools to `confirm_tools` (for example `browser_act`) if you want to approve every action.

## Tools

| Tool | What it does |
|---|---|
| `browser_tabs` | lists tabs as `t1`, `t2`, … and marks the one Kvist works in |
| `browser_open_tab`, `browser_close_tab`, `browser_switch_tab` | tab control |
| `browser_navigate` | an http(s) address, or `back` / `forward` / `reload` |
| `browser_snapshot` | the page as text with a ref for every control (`[e12] button "Next"`) |
| `browser_page_text` | all visible text, for long reading |
| `browser_screenshot` | a picture of the page (the core passes it through the Vision model) |
| `browser_act` | a batch of up to 40 steps: `click` (button, double/triple, `hold_ms`), `hover`, `drag`, `type` (into, clear), `press` (`Ctrl+Shift+T`, `hold_ms`, `repeat`), `scroll`, `wait`, `wait_for` text; `look: snapshot|screenshot` returns a look after the last step |

The model is told to plan from one look: steps it can already foresee go into one batch, and it
looks again only after a step that changes the screen (a new page, a dialog, "Next"). A batch
stops at the first failing step and reports which steps were not run.

## How it is built

`kernel/browser_mcp/`: a standard-library WebSocket client, a CDP and a BiDi driver, the pointer
path (`motion.py`: bowed path, minimum-jerk speed, small overshoot on long reaches), the tools
(`controller.py`, `server.py`) and `page/kvist_page.js`. The page script runs in an isolated world
named `kvist`: it shares the DOM with the page but not its JavaScript, so page scripts cannot see
or fake it; the overlay sits in a closed shadow root in the top layer. Only `http`, `https` and
`about:blank` addresses are opened.
