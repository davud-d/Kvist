# Browser regression tests

Serve Kvist Web first (see ../../README.md). These tests use Playwright already
installed in the development environment; they add no frontend dependencies.

```sh
KVIST_WEB_URL=http://127.0.0.1:9671 node frontends/web-local/tests/browser/redesign.mjs
KVIST_WEB_URL=http://127.0.0.1:9671 node frontends/web-local/tests/browser/zoom.mjs
```

If Playwright or Chromium is installed outside normal Node module resolution, pass
its `index.mjs` and browser executable as the first two arguments, or set
`PLAYWRIGHT_MODULE` and `CHROMIUM_EXECUTABLE`. `KVIST_QA_OUTPUT` sets the evidence
directory (default `/tmp/kvist-web-qa`). Screenshots/reports stay outside the repo.

`redesign.mjs` serves real packaged frontend assets and intercepts the API in its
own isolated browser context. Fixtures cover HTTP request bodies, durable sequence
replay, stream epochs, cumulative streaming, pending approvals, background unread,
selection races, credential renewal, 2,200 messages, 1,200 sessions, 6,000 terminal
lines and a 5,000-line diff. It does not connect to remote SSH or invoke an LLM.

`feed-states.mjs` feeds prepared event sequences through `blocks.js` and `feed.js`, which it
imports in the page from the asset server (`/assets/features/*.js`), into a feed container of
its own. It checks the row states (tool running, ok, failed, opened output, long target, a batch
folded and open, reasoning streaming and finished, plan, notices, a long user message), event
text staying text, keyboard opening, the reasoning timer, rows the person opened staying open,
reduced motion and the 760px column, and writes a screenshot of each state at 1440×900 and
390×844 to `KVIST_SHOTS` (default `/tmp/kvist-shots/3e`; the input states go to its `input/`
directory unless `KVIST_SHOTS_INPUT` says otherwise). Rows without output have no chevron, hover
or pointer; calls show a time from 2 s or when they failed; a group shows its distinct action names
and its failed count; the keyboard focus ring is checked on a tool row, a group and reasoning against
every clipping ancestor and in the screenshot pixels on all four sides. The pinned input panel runs in
the app page with a navigation mock that reports the session as the server does (waiting with its
`pending_input` while a request is open); its checks cover the panel border against focus-within, the
command block and its reason line, the remember flag, the composer placeholder, the secret answer row and
the agent state for every request kind. Every screenshot waits for the
loaded sidebar and two frames, because a shot taken right after a viewport change can catch the
`content-visibility: auto` content of a tool row unpainted.

```sh
KVIST_WEB_URL=http://127.0.0.1:9671 node frontends/web-local/tests/browser/feed-states.mjs
```

`sidebar-states.mjs` runs the app with the API mocked in each of its own browser contexts and checks the
sidebar: no workspace dropdown and no "Sessions" caption, the 28px row and its 14px indicator slot, one shape
and one aria-label for every `execution` value and for unread (waiting over failed over running over unread,
an unknown value drawn empty and warned about once), the weight-600 title of an unread running session,
the group header (24px, name and server 6px apart, shared chevron), the "+" of a group (hover, focus and
`hover: none`, and the workspace it creates in), the New session button, collapsing by click, Enter and Space
with the choice kept in `localStorage` (also with blocked or damaged storage), the indicator of a collapsed
group, the Tab order, the 2px keyboard focus ring on all four sides, reduced motion, no horizontal scroll at
1440, 1280 and 390, the 12px gap between groups (none above the first), the Inbox badge (hidden at 0, "99+" above
99), the ring (2px `--accent` arc over a 25% track, at least 3:1 against `--bg-1`), and the render time of 300 sessions (median of 10 runs, at most 50 ms with
`content-visibility` off). Screenshots at 1440×900 and 390×844 (drawer open) go to `KVIST_SHOTS` (default
`/tmp/kvist-shots/4`).

```sh
KVIST_WEB_URL=http://127.0.0.1:9671 node frontends/web-local/tests/browser/sidebar-states.mjs
```

`status-states.mjs` runs the app with the API mocked in each scenario's own browser context and checks the
bottom rows, the header and the composer: with everything in order the status line is empty and takes no room,
and none of Connecting…, Connected, Idle, Running or Finished is visible in any state; the connection is hidden for
the first 1.5 s of connecting (from the page start and again from each change of state), then shows a 10px ring
with its text; reconnecting shows at once; offline and failed show a cross in `--err`; "Needs input" (`--warn`, two
8px bars) is the only agent state in text and an unknown `execution` is kept, drawn as nothing and warned about once;
the context figure (absent without data, mono 12px, first, 12px between items); the header's path under the title
and the server of a remote session beside it; the open tab (`aria-selected`, 2px underline) and its keyboard ring on
all four sides; the send button (disabled and `aria-disabled` while there is nothing to send, no hover change) and
the hint against the real keys (Ctrl+Enter sends, Enter only breaks the line); no `#composer-tools`,
`#server-switcher` or `#active-server-label` node; the paperclip (28px, colors, inner ring, centred); the hint and
the status line sharing the input's left edge and width; no dot separators; no horizontal scroll at 1440, 1280 and
390. Screenshots at 1440×900 and 390×844 go to `KVIST_SHOTS` (default `/tmp/kvist-shots/5`). The server dialogs,
whose selector is not rendered, open from the command palette in the other scenarios.

```sh
KVIST_WEB_URL=http://127.0.0.1:9671 node frontends/web-local/tests/browser/status-states.mjs
```

`zoom.mjs` uses an isolated temporary Chrome profile and a local test extension to
call `chrome.tabs.setZoom`, verifies the actual zoom and CSS viewport, and exercises
100/110/125% on 1280×720, 1440×900, 1920×1080, 2560×1440. The main regression suite
also checks a 390×844 viewport and narrower zoom-equivalent CSS viewports.

A separate real-controller smoke can use the credential-free core fixture already
provided in `tests/test_local_runtime.py`. The redesign was additionally exercised
that way over real cookies/CSRF, HTTP, SSE, durable history, reload and cancellation;
this is separate evidence from fixture-driven UI tests.

`provider-setup.mjs` starts `setup_server.py` on an ephemeral loopback port and
creates a temporary home directory. HTTP, CSRF and Python configuration storage
are real; only external provider catalog/key-validation requests are stubbed.
It exercises blank startup, AnyModel registration in a new browser tab, invalid
key refusal without persistence, model catalog, reasoning, durable saved defaults,
alternative provider selection, SSH config aliases and five viewport sizes.
The user's credentials and SSH config are never modified. Run from the repo root:

```sh
node frontends/web-local/tests/browser/provider-setup.mjs
```

It accepts the same Playwright/Chromium arguments as the other scripts.
`KVIST_TEST_PYTHON` overrides the default `.venv/bin/python` fixture interpreter.
