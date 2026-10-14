# Visual foundations

Colour, type, space, layout, shape, icons and motion. Every rule here is a
default for when the project and the person have not decided; their system
wins.

## Tokens first

Define every visual value once, by role, and use only the tokens afterwards.
A colour written as a literal in a component is the colour that breaks in the
dark theme.

Two layers work well:

- **Primitives**: the raw scale (`--blue-600`, `--grey-50`, `--space-4`).
- **Semantic tokens**: what components use (`--surface`, `--surface-raised`,
  `--text`, `--text-muted`, `--border`, `--accent`, `--accent-text`,
  `--danger`, `--focus-ring`). A theme redefines only this layer.

Keep the set small. A design with 40 greys has no greys; it has accidents.

## Colour

### Building a palette

- Start from neutrals, not from the accent: background, raised surface,
  border, muted text, text. They carry 90 % of the screen.
- Tint neutrals slightly toward the accent's hue (a chroma of 0.005–0.02 in
  OKLCH). Pure mid-greys look unconsidered next to a coloured accent.
- One accent for interaction (links, primary buttons, focus, selection). A
  second accent is a decision, not a habit.
- Semantic colours (success, warning, danger, info) are separate from the
  accent and used only for state. If the brand colour is red, the danger
  colour still needs to be unmistakable: shift its hue or pair it with an icon.
- Work in **OKLCH** (`oklch(L C H)`) when the stack allows it (all current
  browsers do): lightness there is perceptual, so a scale with even L steps
  looks even, and changing hue at the same L and C keeps contrast roughly the
  same. HSL lightness is not perceptual: `hsl(60 100% 50%)` yellow and
  `hsl(240 100% 50%)` blue have the same L and very different contrast.
- A usable scale: 10–12 steps per hue from L≈0.98 to L≈0.20, chroma highest in
  the middle and lower at both ends (very light and very dark colours cannot
  hold high chroma inside sRGB and get clipped).
- Check gamut: `oklch()` values outside sRGB are clipped by the browser in
  ways you do not control. Keep chroma moderate, or provide an sRGB fallback.

### Contrast

Compute it. Two methods:

- **WCAG 2.x ratio** (the legal standard in most places): text 4.5:1, large
  text (24 px, or 18.66 px bold) 3:1, non-text UI parts (input borders, icons
  that carry meaning, focus indicators, chart lines) 3:1 against what is next
  to them. AAA asks 7:1 and 4.5:1.
- **APCA Lc** (perceptual, better at dark themes and thin fonts; not yet a
  standard, so use it in addition to WCAG, never instead): about Lc 90 for
  body text, Lc 75 as the floor for body text, Lc 60 for other text you read
  (labels, 24 px normal or 16 px bold), Lc 45 for large headings and
  meaningful non-text parts, Lc 30 for placeholders and disabled text, Lc 15
  as the threshold below which a thing is invisible to many people. Sign shows
  polarity: positive is dark text on light, negative is light on dark.

Both in one script. Put the pairs to check (text colour first, then its
background; hex or `oklch(L C H)`) in the first line and pass the whole script
as the single argument of `python3 -c`. Kvist refuses `-c` with any further
argument, and a command longer than about 2000 characters, so check about
five pairs per run:

```python
P = [("#5b5b66", "#ffffff"), ("oklch(0.98 0.01 250)", "oklch(0.52 0.16 250)")]
import math
def rgb(v):
    if v.startswith("oklch("):
        L, C, H = (float(x.rstrip("%")) / (100 if x.endswith("%") else 1) for x in v[6:-1].split()[:3])
        a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
        l, m, s = ((L + p * a + q * b) ** 3 for p, q in ((.3963377774, .2158037573), (-.1055613458, -.0638541728), (-.0894841775, -1.291485548)))
        lin = (4.0767416621 * l - 3.3077115913 * m + .2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s, -.0041960863 * l - .7034186147 * m + 1.707614701 * s)
        return [min(1, max(0, 12.92 * x if x <= .0031308 else 1.055 * x ** (1 / 2.4) - .055)) for x in lin]
    v = v.lstrip("#"); v = "".join(c * 2 for c in v) if len(v) == 3 else v
    return [int(v[i:i + 2], 16) / 255 for i in (0, 2, 4)]
def wcag(f, b):
    def L(c):
        r, g, b = (x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb(c))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    h, l = sorted((L(f), L(b)), reverse=True)
    return (h + 0.05) / (l + 0.05)
def apca(f, b):
    def Y(c):
        r, g, b = (x ** 2.4 for x in rgb(c))
        y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b
        return y + (0.022 - y) ** 1.414 if y < 0.022 else y
    t, s = Y(f), Y(b)
    if abs(s - t) < 0.0005: return 0.0
    if s > t:
        c = (s ** 0.56 - t ** 0.57) * 1.14
        return 0.0 if c < 0.1 else (c - 0.027) * 100
    c = (s ** 0.65 - t ** 0.62) * 1.14
    return 0.0 if c > -0.1 else (c + 0.027) * 100
for f, b in P:
    r = wcag(f, b)
    print(f"{f} on {b}: WCAG {r:.2f}:1 (text {'pass' if r >= 4.5 else 'FAIL'}, large text/UI {'pass' if r >= 3 else 'FAIL'}), APCA Lc {apca(f, b):.1f}")
```

Reference results to trust it by: `#767676` on white is 4.54:1 (the lightest
grey that passes AA on white); `#888` on white is 3.54:1, Lc 63.1; white on
`#888` is Lc −68.5; `oklch(0.628 0.2577 29.23)` is pure red, `#ff0000`.
`oklch()` colours outside sRGB are clipped the way a browser would roughly
clip them. Semi-transparent colours, `color-mix()` and named colours: measure
them where they are painted, with the browser skill's `check_page`, which
reads the colours the browser actually draws.

Check, in every theme: body text on background, muted text on background and
on raised surfaces, text on the accent (button labels), links in body text,
placeholder text, borders of inputs against their surroundings, the focus
ring against both the component and the page, every semantic colour as text
and as an icon, chart series against the background and against each other.

### Dark theme

Design it; do not invert the light one.

- Background is a dark grey, not black: OKLCH L 0.14–0.20 (about `#121212`
  to `#1e1e1e` without hue). Pure black with white text causes halation
  (glowing edges) for many readers, especially with astigmatism.
- Text is off-white (L 0.90–0.95), not `#fff`, for the same reason.
- Elevation goes up by getting lighter: each raised surface 0.03–0.05 L above
  the one below. Shadows barely show on dark backgrounds, so they cannot carry
  depth alone.
- Desaturate and lighten accents: a saturated mid-blue that works on white
  vibrates on dark grey. Raise L, lower C.
- Light text on dark reads heavier; one step lighter weight for body text
  (or a slightly larger size) keeps the same feel.
- Images and illustrations with white backgrounds become glaring panels; give
  them a frame, reduce their brightness slightly, or provide dark variants.
- Respect the system setting (`prefers-color-scheme`), let the person override
  it, remember the override, and keep three states: light, dark, system.

### Colour and meaning

- Never colour alone: an error field also has an icon and text; a chart
  series also has a label, a pattern or a direct annotation; a status dot has
  a word next to it.
- Red/green is the most common confusion (deuteranopia and protanopia, about
  8 % of men). Blue/orange pairs survive it. Check palettes with a colour
  blindness simulator when charts or status depend on hue.
- Keep meaning stable: if green means "running" on one screen, it does not
  mean "selected" on another.

## Typography

### Choosing faces

- One family is often enough; two if they are clearly different (a serif
  display with a sans body, a sans UI with a mono for code). Three is almost
  always one too many.
- Choose for the subject and for the job: a UI face needs open apertures,
  distinct `Il1` and `O0`, tabular figures and a full weight range; a reading
  face needs a comfortable texture at length; a display face needs character
  at large sizes and may be poor at small ones.
- Check the character set before committing: Cyrillic (with `ё`, `й`), Greek,
  Vietnamese diacritics, the `₽` and `€` signs, arrows and quotes your copy
  uses. A missing glyph falls back to another font mid-word.
- The system stack is a legitimate choice for tools, and costs no download:
  `system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", Ubuntu,
  Cantarell, "Helvetica Neue", Arial, sans-serif`. For code:
  `ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo,
  Consolas, "Liberation Mono", monospace`.
- Web fonts: `font-display: swap` (or `optional` for non-essential faces),
  preload the one or two files that render above the fold, subset with
  `unicode-range`, prefer one variable font file over five static ones, and
  give a fallback stack whose metrics are close (`size-adjust`,
  `ascent-override` on the fallback `@font-face` reduce the jump when the
  font arrives).

### Scale and rhythm

- A modular scale gives sizes that relate: ratio 1.125–1.2 for dense UIs,
  1.25 for most products, 1.333–1.5 for editorial pages. From a 16 px base at
  1.25: 12.8, 16, 20, 25, 31, 39, 49. Round to whole pixels or rems.
- Body text on the web: 16 px minimum (17–20 px for long reading). UI labels
  can go to 14 px, captions to 12 px; nothing that must be read below 12 px.
- Line height: about 1.5 for body text, 1.2–1.3 for headings, tighter as size
  grows; serif body text a little more than sans.
- Measure: 45–80 characters per line, about 65 ideal. Use `max-width` in `em`
  or `rem` (about `34em`). Avoid `ch` for layout widths: it follows the
  current font, so the width changes when a web font replaces its fallback
  and the page reflows.
- Space between paragraphs about one line height; space above a heading
  larger than below it, so the heading belongs to what follows.
- Weights: two or three in use (regular, medium or semibold, bold). Hierarchy
  by weight and size together is stronger than by size alone.
- Letter spacing: slightly negative for large display text, slightly
  positive (0.02–0.06 em) for small uppercase labels, zero for body text.
- `text-wrap: balance` on headings, `text-wrap: pretty` on paragraphs where
  supported; avoid a single word on the last line of a heading.
- `font-variant-numeric: tabular-nums` wherever digits are compared or
  aligned (tables, prices, timers, counters), so columns line up and numbers
  do not jitter as they change.

### Typographic details

- Real quotes and apostrophes (“ ” ‘ ’ in English, « » „ “ in Russian), the
  ellipsis character (…), en dash for ranges (10–20), em dash for breaks, the
  multiplication sign for dimensions (1440×900), non-breaking spaces between
  a number and its unit.
- Uppercase only for short labels, with added letter spacing; never for
  sentences, and not as a reflex above every heading.
- Underline only links. Italic for emphasis and titles; bold for warnings and
  keys, sparingly.
- Align text left (start) by default. Centre only short, symmetrical blocks
  (a hero line, an empty state). Justified text on the web makes rivers; use
  it only with hyphenation (`hyphens: auto` and a `lang` attribute).

## Space and layout

- A spacing scale based on 4 or 8 px: 4, 8, 12, 16, 24, 32, 48, 64, 96. Every
  margin, padding and gap comes from it.
- **Proximity carries grouping**: space inside a group is smaller than space
  between groups. If you need a divider to show that two things are separate,
  first try more space.
- Lay out siblings with flex or grid and `gap`; per-element margins collapse
  and double in ways nobody notices until content changes.
- Content width: about 1100–1280 px for app shells, about 680–760 px for
  reading columns. A full-width paragraph on a 2560 px screen is unreadable.
- A 12-column grid is useful for marketing layouts and dashboards; app
  screens are better served by a few fixed regions (sidebar, main, inspector)
  that collapse in a defined order as the window narrows.
- Alignment: every element lines up with something. Optical alignment beats
  mathematical: an icon or a round shape may need 1 px of nudge to look
  centred.
- Let content set heights. Fixed heights on text containers clip translated
  and user content.
- Breakpoints from the content, not from devices: narrow until the layout
  breaks, put the breakpoint there. Typical results land near 480, 768, 1024,
  1280 px. Container queries let a component adapt to its own width rather
  than the window's.

## Shape, depth and surfaces

- Radius by role and size: small controls 4–6 px, cards and inputs 6–12 px,
  dialogs and sheets 12–16 px, pills fully rounded only when they are pills.
  One radius for everything flattens the hierarchy.
- Nested radii are concentric: inner radius = outer radius − the padding
  between them. A card with 12 px radius and 8 px padding holds a 4 px child.
- Elevation means "above": dialogs, menus, popovers, toasts, dragged items.
  Static content does not need a shadow. Shadows look real with two layers, a
  tight darker one and a wide soft one:
  `0 1px 2px rgb(0 0 0 / .08), 0 4px 12px rgb(0 0 0 / .08)`.
- Borders and shadows together: a 1 px semi-transparent border keeps the edge
  crisp where the shadow is faint.
- On a tinted background, tint borders, shadows and muted text toward the
  same hue instead of using neutral grey.
- Each of fill, border, radius and shadow says "this is a separate object".
  Use them on the thing that needs separating, not on every block.

## Icons and images

- One icon set, one stroke weight (1.5 or 2 px at 24 px), one corner style.
  Mixing sets is visible even to people who cannot name why.
- Sizes on a grid: 16, 20, 24 px, aligned to the text's x-height or cap height
  next to them.
- An icon without a label is a riddle except for the handful everyone knows
  (search, close, menu, play). Label the rest, or at least give a tooltip and
  an accessible name.
- Icons do not replace words in navigation of more than four items.
- Images: explicit `width` and `height` (or `aspect-ratio`) so the layout does
  not jump; modern formats (AVIF, WebP) with fallbacks; `srcset` and `sizes`
  for responsive images; `loading="lazy"` below the fold; meaningful `alt` for
  content images, empty `alt=""` for decoration.
- Illustrations and photos carry tone strongly; stock photos of people in
  meetings carry no information. Prefer real screenshots of the product,
  real data, the subject's own objects.

## Motion

Motion explains change: where something came from, where it went, what
caused what. Motion that explains nothing is decoration and is the first
thing to remove.

- Durations: 100–150 ms for small state changes (hover, toggle, press),
  200–300 ms for components entering or leaving (menus, toasts), 300–500 ms
  for large surfaces (sheets, page transitions). Longer feels slow; for a UI
  used all day, shorter.
- Easing: decelerate on enter (`cubic-bezier(0, 0, 0, 1)` or
  `cubic-bezier(0.05, 0.7, 0.1, 1)` for emphasis), accelerate on exit
  (`cubic-bezier(0.3, 0, 1, 1)`), standard for moves within the screen
  (`cubic-bezier(0.2, 0, 0, 1)`). Linear only for progress and continuous
  rotation.
- Exits are faster than entrances (about two thirds of the time).
- Animate `transform` and `opacity`; animating `width`, `height`, `top`,
  `left` costs layout on every frame. Never `transition: all`.
- Anchor transforms where the motion starts (a menu grows from its button).
- Input interrupts animation: a new click must not wait for the old animation.
- One orchestrated moment beats scattered effects. A page where every section
  slides up on scroll reads as generated.
- Reduced motion: under `prefers-reduced-motion: reduce`, replace movement
  with a short cross-fade or nothing; keep the state change itself. Content
  must be visible at rest: never leave content at `opacity: 0` waiting for a
  scroll observer that may never fire.
- Nothing flashes more than three times a second. Autoplaying motion longer
  than five seconds next to content needs a pause control.
- A slow intro reads as a slow computer. If the product has an intro, keep it
  under a second or two, make any key or click skip it, and do not replay it
  every launch.

## Data and charts

- Pick the chart from the question: change over time (line), comparison
  between categories (bar, sorted), part of a whole with few parts (stacked
  bar; a pie only for two or three parts), distribution (histogram, box),
  relation (scatter). A table is often better than any chart for fewer than
  about 20 numbers that people look up.
- One scale for marks, ticks and labels; every label names a value the chart
  reaches; axes start at zero for bars.
- Label directly at the end of lines instead of a legend when there are few
  series. Sort bars by value unless the categories have a natural order.
- Colours from a palette tested for colour blindness, chart text from the
  theme tokens so it reads in both themes, and the highlighted series in the
  accent with the others muted.
- Big numbers ("KPI tiles") only when those figures are the point of the
  screen, each with its period and comparison ("+12 % vs last week").
