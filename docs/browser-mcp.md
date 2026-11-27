# Kvist in your own browser (`kvist --browser-mcp`)

[Русская версия](browser-mcp.ru.md)

A local MCP server that lets Kvist work in the browser you already use: your tabs, your logins.
It connects to the browser's own debugging port, so nothing is installed into the browser.

* **Chromium family** (Chrome, Chromium, Brave, Edge, Vivaldi, Yandex): Chrome DevTools Protocol.
* **Firefox**: WebDriver BiDi (Firefox no longer speaks CDP).

While Kvist works in a tab, the page shows a glowing blue frame, a "Kvist is working here" pill
with the current step and a **Stop** button, and a blue pointer labelled *Kvist*. The pointer is
not a picture laid over the page: Kvist moves the browser's own pointer along the same path, so
the site receives ordinary trusted mouse and keyboard events (hover, press, release, wheel,
key by key typing). Pressing **Stop** ends Kvist's access for the rest of the task.

## 1. Turn on remote debugging

| Browser | How |
|---|---|
| Chrome, Brave, Edge, Chromium | open `chrome://inspect/#remote-debugging` and turn it on (recent versions; your normal profile), **or** start with `--remote-debugging-port=9222` (Chrome 136+ honours the flag only with a separate `--user-data-dir`) |
| Firefox | start with `firefox --remote-debugging-port 9222` |

The server finds a Chromium browser by its `DevToolsActivePort` file first, then tries port 9222.
Desktop apps built on Chromium (Spotify, Electron apps) often hold 9222 too; they are recognised
and skipped. Another port or host goes in `--browser-endpoint 9333` (or `host:port`, or a full
`ws://` address).

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

`sandbox` must be `none`: the server has to reach the browser's local port and its profile folder.
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
