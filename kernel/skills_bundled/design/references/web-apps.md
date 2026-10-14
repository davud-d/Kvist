# Web applications

Implementation guidance for app interfaces in the browser: HTML, CSS and
JavaScript, with notes for React, Vue, Svelte and Tailwind. Websites
(landing pages, docs) have their own page, `websites.md`; forms, tables and
dialogs are in `interaction.md`; accessibility in depth is in
`accessibility.md`.

## Structure

- **Semantic HTML first.** `button` for actions, `a href` for navigation,
  `label` for every control, `table` for tabular data, `nav`, `main`,
  `header`, `footer`, `aside`, headings in order. Native elements bring
  keyboard support, focus, accessible names and browser features for free; a
  `div` with a click handler brings none of them.
- Links are links: navigation uses `<a href>` so Ctrl/Cmd+click, middle click,
  "copy link" and the back button work. A `button` that changes the URL is a
  bug.
- One `h1` per view naming it; a "skip to content" link for keyboard users
  on pages with a long header.
- Use an accessible primitive library (Radix, React Aria, Headless UI, Ark,
  Melt, Bits UI, or the project's) for menus, comboboxes, dialogs, tabs and
  popovers instead of hand-rolling them: their keyboard and screen-reader
  behaviour takes weeks to get right.
- Platform features before scripts: `<dialog>` for modal dialogs (with
  `showModal()` for the focus trap and Escape), the `popover` attribute for
  menus and tooltips, `<details>` for disclosure, `inert` for content behind
  overlays, `<input type="date|email|number|search">` with the right
  `inputmode`.

## Tokens and theming in CSS

Define every colour as a custom property, light values on `:root`, dark
values redefined in two places so the system setting and an explicit choice
both work:

```css
:root {
  --bg: oklch(0.99 0.004 250);
  --surface: oklch(1 0 0);
  --text: oklch(0.24 0.02 250);
  --text-muted: oklch(0.47 0.02 250);
  --border: oklch(0.88 0.01 250);
  --accent: oklch(0.52 0.16 250);
  --on-accent: oklch(0.99 0 0);
  --danger: oklch(0.55 0.19 27);
  --focus: oklch(0.6 0.18 250);
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: oklch(0.17 0.01 250); /* … */ color-scheme: dark; }
}
:root[data-theme="dark"] { --bg: oklch(0.17 0.01 250); /* same dark values */ color-scheme: dark; }
body { background: var(--bg); color: var(--text); }
```

- Components use only tokens. A literal colour inside a component is the one
  that breaks in the other theme.
- `color-scheme` makes native controls, scrollbars and autofill follow.
- Set `<meta name="theme-color">` to the background so mobile browser chrome
  matches.
- Store an explicit choice (light, dark, system) and apply it before first
  paint (a tiny inline script in `<head>`), or the page flashes the wrong
  theme.
- A z-index scale as tokens (`--z-dropdown`, `--z-sticky`, `--z-modal`,
  `--z-toast`) instead of `9999` contests.

## Layout

- Flex and grid with `gap`; intrinsic sizing (`minmax()`, `auto-fit`,
  `fit-content`) over JavaScript measurement.
- Any flex or grid child holding text, code or a table gets `min-width: 0`,
  or long content pushes the layout wider than the screen.
- App shells: header + sidebar + main + optional inspector. Decide the order
  in which regions collapse as the window narrows (inspector first, then the
  sidebar becomes a drawer), and test each step.
- Size full-height apps with `height: 100dvh` (or `100%` on `html, body`),
  not `100vh`, which ignores mobile browser chrome.
- Respect safe areas: `padding: env(safe-area-inset-*)` on edge-fixed bars
  with `viewport-fit=cover`.
- Scroll in one place per region; nested scroll areas trap the wheel. Use
  `overscroll-behavior: contain` on modals and drawers.
- Sticky headers must not cover focused elements or anchor targets: set
  `scroll-margin-top` / `scroll-padding-top`.
- Use logical properties (`margin-inline-start`, `padding-block`, `inset-inline`)
  so a right-to-left language (`dir="rtl"`) mirrors the layout correctly.
- Container queries (`@container`) for components that live in panels of
  different widths.
- Never disable zoom (`user-scalable=no`, `maximum-scale=1` as the only fix).
  Inputs at 16 px or more stop iOS from zooming on focus.

## Loading, latency and feedback

- **Timing**: under 100 ms feels instant, no indicator. 100 ms–1 s: show the
  press (button state). Over 1 s: an indicator. Over 10 s: progress and a way
  to cancel.
- Delay spinners and skeletons about 150–300 ms so fast responses never
  flash them, and once shown keep them for at least 300–500 ms so they do not
  flicker.
- Skeletons mirror the final layout exactly (same heights, same columns), so
  nothing jumps when data arrives.
- A loading button keeps its label (add a spinner beside it), keeps its width,
  and becomes non-repeatable while the request runs; send an idempotency key
  with the request so a double submit cannot double charge.
- **Optimistic updates** when success is very likely (toggling, renaming,
  reordering, sending a message): update at once, reconcile with the server,
  and on failure roll back visibly with the reason and a retry.
- Announce async results to screen readers with a polite live region
  (`aria-live="polite"`) for toasts and inline validation.
- Long lists: virtualise (or `content-visibility: auto`) beyond a few hundred
  rows; paginate or load more with a visible count ("Showing 50 of 1 240").
- Stale data is labelled ("Updated 5 min ago", a refresh control) rather than
  silently shown as current.

## Errors

- Three scopes: a field (inline, next to it), a section (a message in the
  region that failed, the rest of the page works), the whole page (a full
  error view with a way back and a retry). Use the smallest scope that is
  true.
- Render boundaries in component frameworks (React error boundaries,
  `onErrorCaptured` in Vue, `+error.svelte`) so one failing widget does not
  blank the app.
- Network errors distinguish offline, timeout, server error and permission;
  each gets its own message and action.
- Preserve everything the person typed through any error, including a crash
  of a long form (save drafts locally).
- Never show a raw stack trace or JSON error body as the message; keep it in
  a "details" disclosure for support.

## State and URLs

- Put navigational state in the URL: the current view, filters, sort, search
  query, selected tab, page, an open item. Refresh, share, back and forward
  then work without extra code.
- Restore scroll position on back and forward.
- Warn before leaving a page with unsaved changes (and only then).
- Keep `<title>` accurate to the current view, prefixed with a count or
  "Error:" when that is the most important thing.

## Interaction details

- Visible focus on everything focusable, using `:focus-visible` so mouse
  clicks do not show rings; a ring with 3:1 contrast against both the control
  and the page, offset so it is not hidden by the control's own border.
- Hover, active and focus states have more contrast than rest, never less.
- Hit targets at least 24×24 CSS px; on touch 44×44. Extend small icons' hit
  area with padding or a pseudo-element.
- Never block paste. Never trap the person in a field.
- Every drag has a click or keyboard alternative (move up/down buttons, a
  "move to" menu).
- `touch-action: manipulation` on controls removes the double-tap zoom delay.
- Autofocus the one primary input on desktop; rarely on mobile, where the
  keyboard covers half the screen.
- Ellipsis at the end of a menu item that opens a further step ("Rename…").
- Tooltips: a delay before the first, none between neighbours, never the only
  place where essential information lives, never on disabled elements without
  making them focusable.
- Keyboard shortcuts: show them in menus and tooltips, use platform symbols
  (⌘ on macOS, Ctrl elsewhere), and never override the browser's own
  (Ctrl+L, Ctrl+T, Ctrl+W, Ctrl+F unless you provide real search).

## Performance is part of the design

- Targets (Core Web Vitals, 75th percentile of real users): Largest
  Contentful Paint ≤ 2.5 s, Interaction to Next Paint ≤ 200 ms, Cumulative
  Layout Shift ≤ 0.1.
- Reserve space for everything that loads late: images (`width`/`height` or
  `aspect-ratio`), embeds, ads, async panels, web fonts (fallback metrics).
- Animate only `transform` and `opacity`; never `transition: all`.
- Move heavy work off the main thread (Web Workers); batch DOM reads and
  writes; debounce input handlers that do expensive work, but never the
  echo of what is typed.
- Preload the critical font and hero image; lazy-load below the fold;
  `preconnect` to the CDN and API origins.
- Measure on a throttled CPU and network, and on a real low-end phone when
  the audience has them.

## Framework notes

- **React**: stable `key`s from data, never array indexes for lists that
  change; keep inputs responsive (uncontrolled inputs or cheap controlled
  updates; `useDeferredValue` / `useTransition` for expensive filtering);
  `Suspense` boundaries where loading states belong; no layout jumps from
  effects that measure after paint (use CSS, or `useLayoutEffect` sparingly).
- **Vue / Svelte**: the same principles; transitions through the framework's
  transition components honour `prefers-reduced-motion` only if you make them.
- **Tailwind**: put the design tokens into the theme configuration (or CSS
  variables referenced by it) and use those classes; a page of arbitrary
  values (`text-[13px]`, `bg-[#3b3b42]`) is a page without a design system.
  Extract repeated class strings into components, not into `@apply` soup.
  Do not wrap everything in `rounded-lg shadow-md`.
- **Component libraries** (shadcn/ui, MUI, Chakra, Ant, Mantine, Vuetify):
  customise their theme tokens to the product instead of overriding
  individual components; the default theme of a popular kit is a recognisable
  look in itself.

## Dashboards and data-heavy screens

- The summary before the detail: what needs attention now is at the top left
  (top right in RTL), in words and numbers, not only in a chart.
- State in form as well as number: a pill, a chip, a severity stripe, an
  icon; semantic colours separate from the accent.
- Filters and time range visible and in the URL; the current range is always
  shown next to the numbers it affects.
- Every number has a unit, a period and a comparison when one exists.
- Empty and partial data are designed: "No errors in the last 24 h" is
  information, not an empty chart with axes.
- Tables over charts for lookup; charts over tables for shape and trend.
- Density settings (comfortable / compact) for screens used all day.

## Checking a web UI

You do not see the page. The `browser` skill (Linux) gives you three kinds of
evidence; use them for what each is good at.

- `check_page` **measures**: sideways page scroll and the elements causing
  it, clipped text, text contrast below WCAG AA (computed from the colours the
  browser paints, so `oklch()` and transparency are handled), pointer targets
  under 24 px, images without `alt`, fields and buttons without a name,
  heading jumps, a missing `lang`, the character set (anything but UTF-8
  garbles Cyrillic and typographic quotes), fonts that failed to load. Trust
  it over a description.
- `screenshot` with a `question` gets a **description** from a vision model.
  Good for impressions (hierarchy, balance, what stands out, whether it looks
  like the rest of the product); it can miss and invent details, so never use
  it alone for a measurable claim. Ask narrow questions: "Is any text cut off,
  overlapping or touching the edge?", "Which element draws the eye first?",
  "Does anything look misaligned with its neighbours?", "Is it clear which
  item is selected?".
- `snapshot`, `press`, `click` and `console` show **behaviour**: what is
  focusable and named, what a key press does, what errors appear.

A pass over one page:

1. Serve it. Static files: `open_workspace_page`. An app with a dev server:
   start it with `run_shell` `{"background": true, "expose_port": 5173}` (the
   port it prints), wait for it with `job_output`, then `open_page`
   `http://localhost:5173/`.
2. For each of 390×844 and 1440×900, and each of light and dark (when the
   product has both): `emulate`, then `check_page`, then `screenshot` with a
   question. Four combinations for a main screen; one or two for a secondary
   one.
3. Keyboard: `press` Tab repeatedly from the top and `snapshot` or
   `screenshot` with "Which element has the focus ring? Is it clearly
   visible?". Every interactive element is reachable in a logical order;
   Escape closes overlays.
4. `emulate` `reduced_motion: "reduce"`: content is visible at rest.
5. `console` and `network`: a page with errors there is not done.
6. Long content: put a 90-character name, an empty list and many rows into
   the main views (through the app's own forms, or fixture data), and run
   `check_page` again.
7. Report what `check_page` found and fixed, what the descriptions said, and
   what you could not check (zoom to 200 %, a real phone, a screen reader).

Without the browser skill: say that the UI was not seen rendered; check what
the code can prove (tokens only, every state present, a label for every
control, the contrast of token pairs with the script in `visual.md`); ask the
person for screenshots of the main screens at a phone and a desktop width.
