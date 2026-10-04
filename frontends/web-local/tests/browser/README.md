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
