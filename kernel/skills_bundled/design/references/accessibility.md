# Accessibility

Accessibility is whether a person can use the thing at all: with a keyboard
only, a screen reader, a magnifier, voice control, a switch, a tremor, low
vision, colour blindness, migraine, dyslexia, a broken arm, bright sunlight
or a noisy train. It is also, in many countries, a legal requirement (WCAG 2.1
or 2.2 level AA is the usual bar). Treat it as part of correctness, like not
crashing.

## The WCAG 2.2 AA checklist, in practice

Grouped by the four principles. Numbers are the success criteria.

### Perceivable

- **Text alternatives (1.1.1)**: meaningful images have `alt` that says what
  the image conveys in context; decorative images `alt=""`; icon buttons an
  accessible name (`aria-label`, or visually hidden text); charts a text
  summary or a data table.
- **Media (1.2)**: captions for video with speech, transcripts for audio,
  audio description or a text alternative for information shown only visually.
- **Structure (1.3.1)**: headings, lists, tables, labels and landmarks in the
  markup, not only in appearance. Reading order in the DOM matches the visual
  order (1.3.2). Instructions do not rely only on shape, position or colour
  ("the round button on the right") (1.3.3).
- **Orientation (1.3.4)**: works in portrait and landscape.
- **Input purpose (1.3.5)**: `autocomplete` on fields about the user.
- **Colour (1.4.1)**: never the only way to convey information.
- **Contrast (1.4.3)**: text 4.5:1, large text 3:1. **Non-text contrast
  (1.4.11)**: UI component boundaries, states and meaningful graphics 3:1.
- **Resize (1.4.4)**: usable at 200 % text size. **Reflow (1.4.10)**: no
  two-dimensional scrolling at 320 CSS px width (400 % zoom on a 1280 px
  screen), except for content that needs it (tables, maps, code).
- **Text spacing (1.4.12)**: nothing breaks when people override line height
  to 1.5, paragraph spacing to 2×, letter spacing 0.12 em, word spacing
  0.16 em. Fixed heights on text containers fail this.
- **Hover or focus content (1.4.13)**: tooltips and popovers that appear on
  hover can be dismissed (Escape) without moving the pointer, stay while the
  pointer moves over them, and stay until dismissed.
- **Audio (1.4.2)**: no autoplaying sound over 3 s without a control.

### Operable

- **Keyboard (2.1.1)**: everything works with a keyboard alone. **No trap
  (2.1.2)**: focus can always leave a component. **Character shortcuts
  (2.1.4)**: single-key shortcuts can be turned off or remapped, or are active
  only on focus.
- **Timing (2.2.1, 2.2.2)**: time limits can be extended; moving, blinking
  or auto-updating content can be paused.
- **Flashes (2.3.1)**: nothing flashes more than three times per second.
- **Navigation**: skip links or landmarks (2.4.1), page titles (2.4.2),
  logical focus order (2.4.3), link purpose clear from the text (2.4.4),
  more than one way to find pages (2.4.5), descriptive headings and labels
  (2.4.6), **visible focus (2.4.7)**, and **focus not obscured (2.4.11)**:
  sticky headers, footers, cookie banners and chat widgets never fully cover
  the focused element.
- **Pointer (2.5)**: gestures that need a path or several fingers have a
  single-pointer alternative (2.5.1); actions fire on release so a press can
  be cancelled (2.5.2); the accessible name contains the visible label text
  (2.5.3, voice control users say what they see); motion-triggered actions
  have alternatives (2.5.4); **dragging has a single-pointer alternative
  (2.5.7)**; **targets at least 24×24 CSS px (2.5.8)**, or spaced so a 24 px
  circle around each does not overlap another.

### Understandable

- **Language (3.1.1, 3.1.2)**: `lang` on the page and on passages in another
  language, so screen readers pronounce them correctly.
- **Predictable (3.2)**: focus or input alone never triggers a change of
  context (no auto-submitting selects, no navigation on focus); navigation and
  naming are consistent across pages; **consistent help (3.2.6)**: help links
  and contact options sit in the same place on every page.
- **Input assistance (3.3)**: errors are identified in text (3.3.1); labels
  or instructions where input is required (3.3.2); suggestions for fixing
  errors (3.3.3); legal, financial and data-changing submissions can be
  reviewed, corrected or reversed (3.3.4); **redundant entry (3.3.7)**: do not
  ask for the same information twice in a flow; **accessible authentication
  (3.3.8)**: no cognitive tests to log in (allow paste and password managers,
  offer alternatives to puzzles).

### Robust

- **Name, role, value (4.1.2)**: custom components expose them (prefer
  native elements, which do automatically). **Status messages (4.1.3)**:
  announced without moving focus (`role="status"`, `aria-live="polite"`).

## Keyboard in practice

- Tab order follows the visual order; no positive `tabindex`.
- Composite widgets (menus, tabs, listboxes, grids, toolbars) take one Tab
  stop and move inside with arrows (the WAI-ARIA Authoring Practices
  patterns).
- Focus is visible, at least 2 px, 3:1 against adjacent colours, and not
  removed with `outline: none` without a replacement.
- Opening a dialog moves focus into it; closing returns focus to the trigger;
  deleting the focused item moves focus to the next item, not to the page top.
- Route changes in single-page apps move focus to the new view's heading (or
  announce it), or screen reader users do not know the page changed.

## Screen readers

- Test with at least one: NVDA (Windows, free) with Firefox or Chrome,
  VoiceOver (macOS and iOS, built in) with Safari, TalkBack (Android), Orca
  (Linux).
- Use ARIA only where HTML has no equivalent; wrong ARIA is worse than none.
  `aria-label` overrides visible text, so make it contain that text.
- `aria-hidden="true"` on decorative icons; never on focusable elements.
- Live regions exist in the page before content is put into them.
- Tables with headers (`th`, `scope`), forms with labels and grouped
  `fieldset`/`legend` for radio groups.
- Hide visually but keep for screen readers with a `.visually-hidden` class
  (clip pattern), not `display: none`.

## Motion, vestibular disorders and attention

- `prefers-reduced-motion: reduce` removes parallax, zooms, large movements
  and auto-advancing carousels; keeps fades and the state change.
- No content that moves by itself for more than 5 s without a pause control.
- Avoid scroll hijacking and smooth-scroll overrides.

## Cognitive accessibility

- Plain language, short sentences, one idea per paragraph, familiar words.
- Consistent layout and naming; no surprise changes of context.
- Show steps and progress in multi-step tasks; let people save and return.
- Do not rely on memory: show previous answers, recent items, examples.
- Generous time; warnings before timeouts with an option to extend.
- Error prevention and easy recovery (undo).

## Low vision and zoom

- Test at 200 % and 400 % browser zoom; with the OS text size increased;
  with Windows High Contrast / forced colours (`@media (forced-colors:
  active)`: use system colours, keep borders on controls, never rely on
  background colours alone for state).
- Icons and focus rings use `currentColor` or tokens so they survive forced
  colours.

## Internationalisation is accessibility too

- Text expands: Russian and German 15–35 % longer than English, some UI
  strings double. Leave room, let containers grow.
- Right-to-left languages (Arabic, Hebrew): `dir="rtl"`, logical CSS
  properties, mirrored icons that show direction (back arrows), unmirrored
  ones that do not (play, clocks, logos).
- Locale-aware formats for dates, numbers, currency (`Intl.DateTimeFormat`,
  `Intl.NumberFormat`, `Intl.PluralRules`).
- Never build sentences by concatenating translated fragments; word order and
  grammar differ.

## Testing accessibility

1. Automated: axe (browser extension, `@axe-core/playwright`, or Lighthouse).
   It catches roughly a third of problems; zero findings is a start, not a
   pass.
2. Keyboard walk of every task, with the mouse unplugged in spirit.
3. Zoom 200 % and 400 %, and a 320 px wide viewport.
4. Contrast computed for every colour pair in both themes (`visual.md`).
5. A screen reader pass of the main task: headings list, landmarks, form
   labels, buttons named, status announced.
6. Reduced motion and forced colours emulated.
7. Report what was tested and how; never claim "accessible" from an
   automated scan alone.
