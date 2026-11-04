# kvist.site — the landing page

Four static pages — `/` and `/install/` in English, `/ru/` and `/ru/install/` in
Russian — built by one Node script. No framework, no CSS library, no
third-party request at runtime.

Every command below is written for **fish**.

## Build and look at it

```fish
cd web
npm install
node build.mjs --serve
```

`http://localhost:4173/`, `/ru/`, `/install/` and `/ru/install/`. `node build.mjs`
alone builds into `dist/` and stops.

The build refuses to finish if a gate fails: the two locales must have the same
key set, every text token pair must clear 4.5:1, the hero animation must clear
4.5:1 behind the hero text, and the four weight budgets must hold. A screenshot
legend with a different number of lines than its outlined boxes also fails it.

## Layout

```
web/
  build.mjs              renders the four pages into dist/, checks the budgets
  src/
    tokens.css           every colour on the site, and nothing else has one
    base.css             reset, type, page frame, grain and scanline texture
    components.css       header, hero, cards, install page, footer
    template.mjs         both pages, rendered once per locale; the outlined
                         boxes of each screenshot are the SHOTS table there
    i18n/*.json          copy; installPage drives /install/
    font5x7.json         the terminal's bitmap font, exported from banner.py
    js/hero-frames.js    the banner animation, ported from banner_frames.py
    js/hero.js           draws those frames into the hero canvas
    js/main.js           theme, language bar, reveal, copy buttons, nav state
  assets/
    fonts/               JetBrains Mono subsets, latin and cyrillic
    demo/record.fish     re-records the front page's two casts
    install/             the install page's screenshots (WebP, 1500 px wide)
    demo/seed-config.py  the throwaway config a recording starts from
    demo/stub_gateway.py the local gateway the recordings talk to
    demo/fixture.py      the throwaway workspace the demo runs against
    demo/drive.py        types the goal into the TUI over a pty
    demo/run-*.cast      the raw asciinema recordings
    demo/run-*.json      the compiled replays the front page loads
  tools/
    contrast.mjs         WCAG over every token pair, plus the border floor
    hero-contrast.mjs    the hero text against the animation, frame by frame
    i18n-check.mjs       locale key parity
    cast-compile.mjs     cast -> compact replay JSON
    vt.mjs              the terminal emulator cast-compile runs the cast through
    og.mjs               the social card and the tab icon
    audit.mjs            widths, themes, no-JS, reduced motion, keyboard
    perf.mjs             LCP, CLS and bytes under Fast 3G
  dist/                  the built site — this is what gets deployed
  deploy/                the nginx snippet the deploy includes, generated too
```

## Changing the palette

Everything is in `src/tokens.css`. Nothing else on the site — not a component,
not the canvas, not the SVG texture, not the terminal replay — names a colour.
Repainting the whole site is an edit to that one file.

The dark values are the starting point; light is the same roles inverted. Both
live in that file, and so do the sixteen ANSI slots the recorded run is painted
with, so a repaint reaches the demo too.

After any edit:

```fish
node tools/contrast.mjs
node tools/hero-contrast.mjs
```

The first prints every token pair that carries text and exits non-zero below
4.5:1. It also checks that no dark-theme border falls below the `#1a1a1a`
brightness floor — below that a divider disappears on a display at brightness
35. The second replays the hero animation and measures the worst frame behind
the hero text. Both run again inside `node build.mjs`.

Accents are budgeted: five places on the whole page, listed by name in a
comment in `tokens.css`. Adding a sixth is a decision, not a detail.

## Retaking the install page's screenshots

They are real screens, not mock-ups. Take them on a private X server, never the
desktop: `Xvfb :97`, a `kitty` on it, the release `install.sh` served by
`scripts/release/mock_release_server.py` (`KVIST_RELEASE_URL` points the script
at it), and a clean HOME. Run the installer inside `bwrap --tmpfs /home --bind
<scratch> /home/dev` so every path on screen reads `/home/dev/...`, not yours.
Grab the window with `import -window <id>`, convert to WebP into
`assets/install/`, and set each image's outlined boxes in `SHOTS`
(`src/template.mjs`): pixels in that image, one box per legend line.

The last two screens (`start`, `task`, `commands`) run the real `kvist` against
`demo/stub_gateway.py` on port 20129 with a config from
`demo/seed-config.py <home> ready`, which is why they name a local provider.

## Re-recording the front page's run

The recording is a real Kvist run against a throwaway workspace in `/tmp`. It
is not edited — a retry, a tool error or a refusal stays in.

```fish
cd web/assets/demo
./record.fish          # both locales
./record.fish en       # one
```

You need `asciinema` 2.x on `PATH` and a configured Kvist provider; the script
reads nothing from `~/.kvist/config.json` itself, it just runs `kvist`.

`record.fish` builds the fixture, drives the TUI over a pty with `drive.py`,
and compiles the result. To compile an existing cast by hand:

```fish
node tools/cast-compile.mjs assets/demo/run-en.cast assets/demo/run-en.json
```

The compiler runs the cast through a terminal emulator (`tools/vt.mjs`), keeps
only the rows that change, and stores each distinct row once. That is what
turns a 5 MB cast into roughly 5 KB over the wire. It also drops the tail after
`/bye` — the driver's own teardown — so the poster frame is the run's result.

Colours are snapped to the sixteen palette slots at compile time, so a cast
recorded in any terminal theme paints in the site's.

## Checks

```fish
node tools/audit.mjs   # widths, both locales, both themes, no-JS, reduced motion, keyboard
node tools/perf.mjs    # LCP, CLS, transferred bytes under Fast 3G
npx html-validate dist/index.html dist/ru/index.html \
                  dist/guides/index.html dist/ru/guides/index.html
```

`audit.mjs` and `perf.mjs` use the system Chrome when one is installed rather
than downloading a second copy.

## Deploying

`dist/` is the document root. Nothing is generated at request time, so any
static host serves it.

`deploy/security-headers.conf` is written by the build. It carries the CSP, HSTS
and the rest, and it is generated rather than kept here because the policy names
the digest of the inline theme script — a hand-copied digest goes stale the
first time that script is edited, and a stale digest is a page that stops
theming itself. On nginx, include it in every `location` that sets a header of
its own: `add_header` does not inherit into a block that adds headers, so a
`location` with a `Cache-Control` line and no `include` silently ships no CSP.

Nothing is content-hashed, so HTML should stay fresh (`max-age=0,
must-revalidate`) while assets revalidate. The `location` for HTML must also
match `index.html`: `index` redirects the directory URI to the file internally
and the location is matched again on the file, so matching only the directory
means its `Cache-Control` never ships. Fonts can be cached as immutable.

Behind a CDN, keep HTML minifiers and script deferral (Cloudflare's Rocket
Loader and the like) off: the theme decision is an inline script in `<head>`
that has to run before the first paint, and deferring it brings back the white
flash. If the origin only accepts the CDN, test the proxy's address
(`$realip_remote_addr` on nginx), not the visitor's one that `real_ip` puts
into `$remote_addr`, or the allow list refuses everyone.

## Things worth knowing before editing

- The install command must stay visible without scrolling at 1366×768.
  `audit.mjs` asserts it.
- The hero canvas is an enhancement. The headline, the lede and the command are
  in the first HTML response, and the demo's final frame is rendered into the
  `<pre>` at build time, so the page is complete with JavaScript off.
- `?hero=` is not a thing; the intro runs on its own clock. To inspect the
  animation, use the browser's animation inspector or lower `--hero-settle`.
- Widths are stated in `em`, never `ch`. A `ch` resolves against whichever font
  is active, so it measures one thing before the webfont arrives and another
  after — which reflows paragraphs under the reader. That single unit was most
  of the Russian pages' layout-shift budget.
- The replay is set at the page's 14 px floor wherever 100 columns fit. Below
  that it shrinks and the right-hand telemetry columns are clipped — a
  horizontal scrollbar inside the page would be worse.
- `src/font5x7.json` is exported from the repository's `banner.py`. If the
  terminal's font changes, re-export it; the hero and the social card both read
  from it.
