# Checking the site

A site is finished when you have opened it and looked, not when the files are
written. The `design` skill's `web-apps` page, section "Checking a web UI",
describes the browser tools (`emulate`, `check_page`, `screenshot`, `console`,
`network`) and the order to use them; follow it. This page adds what is
particular to a website.

## Serving it

Open the site the way a visitor will, over HTTP, because root-relative links,
fonts and some browser features behave differently from `file://`. A plain
`python3 -m http.server` in the site folder is enough.

Each sandboxed command has its own network namespace. A server started by one
command is unreachable from another unless both run with `network: true`, so
start the server in the background with that flag, then open it with the
browser skill. If the browser skill cannot reach it, say so in the report; do
not claim a visual check you did not make.

## What to check

1. **Three widths**: 360, 768 and 1280 pixels. Nothing scrolls sideways,
   nothing is cut off, text stays readable without zoom, tap targets are big
   enough to hit.
2. **The first screen** at each width answers what the site is, who it is for,
   and what to do next, as the `design` skill's `websites` page puts it.
3. **Every link and image**: internal links reach a page that exists, anchors
   land on a heading, images load, nothing returns 404 (`network`).
4. **Console**: no errors, no warnings you can fix.
5. **Keyboard**: Tab reaches every control in a sensible order and the focus
   is always visible.
6. **Dark mode and reduced motion** if the site supports them: content is
   readable and visible at rest.
7. **Contrast** of body text, links and buttons against their backgrounds, in
   each theme.
8. **The title and description** read well as a search result and a shared
   link.

Fix what you find and check again; one pass that finds nothing is the stop
condition, not the first pass that finds something.

## What to report

State what you measured and what it showed, in a few lines, and separate three
things: *checked* (and the result), *fixed* (what was wrong), and *not
checked* (and why: no browser available, a feature needing a real device or a
live backend). The person decides whether to publish on this report, so an
unchecked item stated as checked is the worst thing it can contain.
