# Kvist Cloud on a phone (`/m`)

*(Русская версия: [README.ru.md](README.ru.md))*

The phone page of Kvist Cloud: a chat with the agent, built for what the cloud sells (hours, a machine that sleeps and wakes,
projects, a model key) rather than for a generic assistant. Black and grey, one typeface, no gradients; colour appears only
for states (working, done, failed). Plain files, no bundler: `cloud/frontend/build.mjs` copies this folder to `dist/mobile` and
the server serves it at `/m`, `/m/s/<session id>` and `/m/p/<project id>` (one page for all of them).

Behind everything is the night sky ([`js/stars.js`](js/stars.js)): stars all around the phone in 3D, drawn at the screen's own
pixel density. The phone is a window into it: turning the phone looks around the sky (the gyroscope; iOS asks once, on the
first touch, and the iOS app grants it to the cloud's own page), and the near stars drift slowly past the far ones. Without a
gyroscope the view turns by itself, very slowly. The pointer (a mouse, or a finger while it touches) reaches in too: the eye
moves a little after it, so near stars slide against far ones, the stars around it part and brighten, and a click or a tap
sends a wave through them; under reduced motion the sky stands still; a hidden page draws nothing.

It runs in any phone browser (and can be added to the home screen), and inside the Android and iOS apps
([`frontends/cloud-android`](../../frontends/cloud-android), [`frontends/cloud-ios`](../../frontends/cloud-ios)), which add the
operating system's own payment sheets.

## Screens

* **Start** (`/m/`): what the agent is for, the project it will work in, what is still to set up (plan, model key, project) or four
  things to ask it. The composer is docked at the bottom, never floating over the conversation. Sending the first message
  plays one short flight (370 ms, [`js/flight.js`](js/flight.js)): the text folds, flies to its place and unfolds, and the start
  screen's words drift away; nothing blocks touches, and it is off under reduced motion.
  The heading in the middle speaks while the box is empty ([`js/hints.js`](js/hints.js), the same file as the workspace's,
  where it is the field's placeholder; the box at the bottom keeps its own words): its words are erased and a line is typed out letter by
  letter, wrapping to two rows at most (the room for both is kept, so nothing moves), stays (the first one and every line about now for 2–3 minutes, others sometimes only 15–25 s, never three
  short ones in a row), and is erased in quick bursts before the next. Lines about now come every other time while they
  hold: the hour, the weekday, dates of the year, the person's tasks, the trial's last day, a year with Kvist, a first
  visit or a return after a week; three lines to each, each said once a visit. A line that does not fit is skipped. A touch or a key in the field stops it with the whole line standing; a hidden page stops it, and coming
  back types a new line. Under reduced motion lines change whole.
* **Session** (`/m/s/<id>`): your messages, the agent's answers as flat Markdown, and its work folded into one line per turn
  ("Worked for 2 min · 6 steps") that opens into the steps. A question from the agent (approve a command, confirm, pick) stands
  where the composer was. The round button is *send*, and *pause* while the agent works and the box is empty.
* **Drawer**: sessions by day or by project, and the account strip with the hours left.
* **Sheets**: account (hours, machine, model key, language), plans, paying, model key (a searchable provider list), new project.

## Paying with the phone's wallet

The page never holds a card. In the apps it asks the server for a checkout (`POST /api/wallet/checkout`), the server names the price
and builds the sheet's request, the app opens Google Pay or Apple Pay with it and returns a token, the page sends the token to the
server (`POST /api/wallet/pay`), the server charges it through Stripe and credits only what Stripe confirms
([`wallet.py`](../backend/kvist_cloud/wallet.py)). In a plain browser there is no wallet and the other ways to pay (Platega) show.

The apps give the page `window.KvistHost` ([`js/native.js`](js/native.js) is the one place that uses it):

| Call | Arguments | Answer |
| --- | --- | --- |
| `wallets` | | `["google_pay"]` or `["apple_pay"]` or `[]` |
| `pay` | `{wallet, request, environment}` | `{token}` or `{cancelled: true}` or `{error}` |
| `payDone` | `{wallet, ok}` | nothing (Apple's sheet waits for it; Google's closes by itself) |
| `signIn` | | `{started: true}` once the app has taken the sign-in over (the system browser signs in, the app reloads the page signed in); `null`: the page signs in the ordinary way |
| `open` `copy` `share` `haptic` | `{url}` `{text}` `{text}` `{kind}` | nothing |

## In an IDE panel

The VS Code and JetBrains side panel switches between "Cloud" and "Local" at the top (the switch and its motion are in
[`frontends/ide/vscode/webview/cloud.tsx`](../../frontends/ide/vscode/webview/cloud.tsx), a three-beat curve of 50, 75 and 30 ms).
The cloud there is this page, framed, except that the composer does not fold to one row when focused: there is no on-screen keyboard.

A frame has no `KvistHost`; the panel around it is the host, with the same calls over `postMessage` (`{kvistCloud: {id, name, args}}` up,
`{kvistCloudReply: {id, result}}` back). It answers `signIn`, `open` and `copy`, and anything else with `null`. Signing in: GitHub's page
refuses frames, so the plugin opens `/auth/app/start?nonce=N&port=P` in the system browser, the cloud hands the code to the plugin's
listener at `http://127.0.0.1:P/signed-in` (RFC 8252), and the frame trades the code and N for its cookie. That is a separate
`kc_ide_session`, `SameSite=None; Secure; Partitioned`, because a Lax cookie never reaches a cross-site frame. Other sites get nothing
from it: it is keyed to the IDE's top-level page, the API wants `X-Kvist-Client`, and the WebSocket checks Origin. Only `/m` may be framed,
and only by `vscode-webview:`, `vscode-file:` and `file:` (the JCEF page); the rest of the site stays `frame-ancestors 'none'`.
The cloud's address is `kvist.cloudUrl` in VS Code and `-Dkvist.cloud.url` / `KVIST_CLOUD_URL` in JetBrains; `https://cloud.kvist.site` by default.

## Tests

```bash
cd cloud/mobile && npm ci && npm test            # unit tests: Markdown, the event feed, rules, formats, translations

# The page in a real browser against a real server (tests/server.py: a real runner, the scripted Kvist, a fake Stripe):
cd cloud/frontend && npm ci && node build.mjs
cd ../mobile && npx playwright install chromium webkit
PYTHON=<python with cloud/backend and cloud/runner installed> WEBKIT=1 node tests/browser/run.mjs
```

`KVIST_ONLY='<regex>'` runs some scenarios, `SHOTS=<dir>` keeps a screenshot of each. `PLAYWRIGHT_MODULE` and
`CHROMIUM_EXECUTABLE` point at an installed Playwright and browser. The scenarios drive the first run, a question from the agent,
pausing, buying a plan with each wallet, a declined card and a closed sheet, a plain browser, Russian, and every phone width from
320 to 430 px. The workflow [`cloud-mobile.yml`](../../.github/workflows/cloud-mobile.yml) runs these and the two apps' tests
and nothing else.
