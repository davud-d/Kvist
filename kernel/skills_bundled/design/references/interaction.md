# Interaction patterns

Patterns that recur in every kind of interface, with the choices that make
them work. Most apply equally to web, desktop, IDE panels and TUIs; where a
terminal differs, see `terminal.md`.

## Forms

### Structure

- Ask only what you need now. Every field costs time and some people's
  trust; optional fields are marked "optional" (rather than marking required
  ones with asterisks, when most fields are required).
- One column. Labels above fields (fastest to scan, survives translation and
  narrow screens). Group related fields under a heading.
- A visible label for every control; a placeholder is not a label (it
  disappears when typing and usually fails contrast). Placeholders show an
  example of the format, ending with an ellipsis or as "e.g. …".
- Size fields to their expected content: a postcode field is short, an
  address field is long. The size is a hint.
- Long forms: split into steps by topic, show progress ("Step 2 of 4"), allow
  going back without losing answers, and keep a summary before submit.
- Defaults that are right for most people, preselected; never a preselected
  choice that benefits the product at the person's expense (newsletter
  consent).
- The right input types (`email`, `tel`, `url`, `number` only for quantities,
  not for codes or card numbers), `inputmode`, `autocomplete` attributes
  (`name`, `email`, `street-address`, `one-time-code`, `new-password`), no
  spellcheck on codes and usernames.
- Support password managers (real `form`, `name`/`autocomplete` attributes,
  no blocked paste) and pasting one-time codes into split fields.

### Validation

- Be liberal in what you accept: trim spaces, ignore hyphens and spaces in
  card numbers and phone numbers, accept dates in unambiguous formats, accept
  names with apostrophes and diacritics, any case in emails.
- Validate when the person tries to move on (submit, next step), not on every
  keystroke and not the moment they leave a field. Inline real-time feedback
  only where it helps while typing (password rules, character counts,
  availability of a username).
- Do not disable the submit button to signal invalidity: let people submit
  and then show what is missing. A disabled button explains nothing.
- On error: keep everything typed; put the message next to the field, in
  text and with an icon (not only red); move focus to the first error (or to
  an error summary at the top that links to each field on long forms).
- Messages say what to do: "Enter a date in the past", not "Invalid date".
- Server-side validation always; client-side only as a convenience.
- Submit: Enter submits a single-field form; in a textarea, Enter is a new
  line and Ctrl/Cmd+Enter submits. While submitting, the button shows
  progress and ignores repeats.

## Buttons and actions

- One primary action per view, visually distinct; secondary actions quieter;
  destructive actions in their own colour and not adjacent to the primary.
- Labels are verbs that say exactly what happens: "Create invoice", "Delete
  3 files", "Send 12 invites". Not "OK", "Submit", "Yes".
- Order follows the platform: on Windows and the web the primary usually
  comes first (left) in dialogs; macOS puts the primary on the right. Keep one
  convention within a product.
- A button that is unavailable either explains why (helper text, tooltip on a
  focusable element) or is not shown.
- An action that opens a further step ends with an ellipsis ("Export…").

## Confirmation, undo and destructive actions

- **Prefer undo to confirmation.** Do it at once, show a toast with "Undo"
  for several seconds, and really make it undoable. Confirmations are clicked
  through by habit; undo protects without slowing anyone down.
- Confirm when undo is impossible (sending, publishing, deleting externally,
  paying). The dialog names the object and the consequence ("Delete project
  'atlas'? Its 12 environments and their data are deleted for good.") and the
  button repeats the verb ("Delete project").
- Scale to severity: for irreversible, large actions, ask the person to type
  the name of the thing.
- Never put a destructive action where a habitual one usually is.

## Dialogs, drawers, popovers and inline editing

- **Inline** for small edits in place (rename, toggle, quick value). Least
  disruptive; prefer it.
- **Popover** for a small choice tied to one control (a date, a colour, a
  menu). Closes on outside click and Escape.
- **Drawer / side sheet** for detail or editing while keeping the list in
  view.
- **Modal dialog** only when the person must decide before anything else can
  continue (unsaved changes, irreversible actions, a required choice). Focus
  moves into it, is trapped while it is open, and returns to the trigger on
  close. Escape closes unless closing would lose work.
- Never stack modals on modals. A wizard in a modal is usually a page.
- A dialog has a title that says what it is about, a body that is short, and
  buttons that say what they do.

## Feedback and notifications

- **Inline status** for the result of an action in place ("Saved" next to
  the field). Best when the person is looking there.
- **Toasts** for confirmations and undo of actions elsewhere; short, a few
  seconds, pausable on hover and focus, never the only record of an error
  that needs action, dismissible, announced to screen readers politely, not
  covering the primary controls.
- **Banners** for a state that persists (offline, trial ending, maintenance),
  at the top of the region they concern, dismissible when the state allows.
- **Badges and counters** for things waiting; clear them when seen.
- **System notifications** only for what matters when the person is
  elsewhere (a long job finished, a mention), only after they agreed, and
  grouped.
- Visibility of state: a busy indicator in the place that is busy (the row,
  the button, the panel), not a global spinner that blocks everything.

## Timing reference

| Wait | Feedback |
| --- | --- |
| < 100 ms | none; show the result |
| 100 ms – 1 s | the pressed state; no spinner (it would flash) |
| 1 – 10 s | a spinner or skeleton with a word on what is happening |
| > 10 s | progress (percent or count), elapsed time, a way to cancel; say when it will finish if known |
| unknown, long | running counts ("1 240 files scanned"), the current item, and a way to continue in the background |

Show indicators after a delay of about 150–300 ms and keep them at least
300–500 ms once shown. Status messages that replace each other stay at least
about 2 s each.

## Lists and tables

- Tables for comparing many items on several attributes; cards for items that
  are mostly visual; plain lists for items with one main attribute.
- Columns: the identifying column first and sticky when scrolling sideways;
  numbers right-aligned with tabular figures; units in the header, not in
  every cell; text left-aligned; dates in one format.
- Sorting by clicking headers with a visible arrow; the current sort is
  always visible; a sensible default sort (most recent, most important).
- Filters visible above the table with the active ones shown as removable
  chips and a "clear all"; counts of results; filters in the URL.
- Row actions: a primary action on row click (open), others in a menu at the
  row's end; bulk actions appear when rows are selected, with the count
  ("3 selected").
- Empty table: say why (no data yet, or no matches for these filters, with a
  button to clear them).
- Long text truncates with an ellipsis and shows the full value on hover or
  focus, or wraps when the column is the main content.
- Pagination when people need to refer to positions or share pages;
  "load more" for browsing; infinite scroll only for feeds, never above a
  footer people need.
- Density: comfortable by default; compact for expert, all-day screens; a
  setting when both audiences exist.

## Navigation

- People should always know where they are (the current item highlighted,
  the page title, breadcrumbs in deep hierarchies), where they can go, and
  how to get back.
- Top-level navigation of 3–7 items with words; icons alone only for the
  universally known.
- Sidebars for apps with many sections; tabs for switching views of the same
  object; breadcrumbs for hierarchies deeper than two levels.
- The back button always works and returns to the previous state, including
  filters and scroll position.
- A command palette (Ctrl/Cmd+K) for products with many commands: it lists
  every command with its shortcut, searches by words people use (synonyms),
  and shows recent commands first.

## Search

- A visible search field where people expect it (top of the page or
  sidebar), with `/` or Ctrl+K as a shortcut.
- Results update as typed for local data; after a pause of about 150–300 ms
  for server queries; the query is kept when going back.
- Tolerate typos, case, diacritics and keyboard-layout mistakes
  (a Russian layout turns "query" into "йгукн").
- Highlight the match in results; show counts; group by type when results
  are mixed.
- No results: say so, show the query, suggest a correction or a broader
  search, and offer what they might want instead.

## Selection and keyboard

- Click selects; Shift+click extends; Ctrl/Cmd+click toggles; Ctrl/Cmd+A
  selects all in the focused list. Keyboard: arrows move, Shift+arrows
  extend, Space toggles.
- Shortcuts follow platform conventions (Ctrl/Cmd+S save, Z undo, Shift+Z or
  Y redo, F find, W close tab). Single-letter shortcuts only when focus is not
  in a text field, and with a way to turn them off (they break voice control
  and some assistive tech).
- Show shortcuts next to commands in menus, tooltips and the palette.

## Drag and drop

- Always with an alternative (buttons, menu, keyboard).
- A clear handle; the dragged item follows the pointer; the drop position is
  previewed; invalid targets say why; Escape cancels.
- Dropping files: highlight the whole drop zone on drag-over, accept the file
  types you can handle, say what was rejected and why.

## File upload

- A button and a drop zone; accepted types and the size limit stated before
  choosing; progress per file with cancel; a preview where useful; errors per
  file with the reason ("too large: 24 MB of 10 MB allowed").
- Uploading does not block the rest of the form.

## Settings

- Grouped by what people want to change, searchable when there are many.
- Changes apply at once and say so ("Saved"), or there is one clear Save with
  a warning on leaving with unsaved changes. Do not mix the two models on one
  screen.
- Each setting: a label, a one-line explanation of its effect, the current
  value, and the default marked.
- A way to reset a section to defaults.
- Dangerous settings (delete account, reset everything) at the bottom,
  separated, with confirmation.

## Wizards and multi-step flows

- One topic per step, progress shown, back without losing answers, a summary
  before the irreversible step, the ability to leave and resume.
- Skip what does not apply (do not show an empty step).
- The last step says what happened and what to do next, then goes there.

## Empty states

- Say what this place is for, why it is empty now (first use, no results,
  everything done, no permission), and the one action that fills it.
- "All done" is a good empty state: say it plainly ("No failing tests").
- An illustration is optional; the action is not.

## Real-time and collaboration

- Show who else is here and what they are editing; never overwrite another
  person's change silently (merge, or show a conflict with both versions).
- Connection state is visible when it matters (offline, reconnecting, saved
  to server).
- Changes made by others appear without scrolling the person's view away.
