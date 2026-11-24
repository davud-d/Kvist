# Kvist Cloud on a phone (`/m`)

*(Русская версия: [README.ru.md](README.ru.md))*

The phone page of Kvist Cloud: a chat with the agent, built for what the cloud sells (hours, a machine that sleeps and wakes,
projects, a model key) rather than for a generic assistant. Black and grey, one typeface, no gradients; colour appears only
for states (working, done, failed). Plain files, no bundler: `cloud/frontend/build.mjs` copies this folder to `dist/mobile` and
the server serves it at `/m`, `/m/s/<session id>` and `/m/p/<project id>` (one page for all of them).

It runs in any phone browser (and can be added to the home screen), and inside the Android and iOS apps
([`frontends/cloud-android`](../../frontends/cloud-android), [`frontends/cloud-ios`](../../frontends/cloud-ios)), which add the
operating system's own payment sheets.

## Screens

* **Start** (`/m/`): what the agent is for, the project it will work in, what is still to set up (plan, model key, project) or four
  things to ask it. The composer is docked at the bottom, never floating over the conversation.
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
