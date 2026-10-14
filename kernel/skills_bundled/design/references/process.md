# Process, critique and audits

How to go from a request to an interface someone can use, how to judge one
that exists, and how to measure whether a change made it better.

## 1. Read the request

Sort the request before designing, because the treatment differs:

| Kind | Examples | Treatment |
| --- | --- | --- |
| Utility | an admin table, a settings page, a CLI's output, a report | Clarity, density, speed of scanning. Quiet typography, one accent, no hero. |
| Product UI | an app's main screens, a dashboard, an editor, a TUI | Information design first: the summary before the detail, state visible at a glance, the main action obvious. |
| Editorial | a landing page, a portfolio, a launch page, a game | A point of view. One memorable idea, carried out precisely. |
| Flow | onboarding, checkout, install, a wizard | The fewest steps, one decision per step, progress and a way back. |
| Fix | "the button is ugly", "this is confusing" | Find the cause (hierarchy, wording, state, contrast) before restyling. |

When unsure, a well-composed utility treatment is never wrong; an
over-designed one sometimes is.

## 2. Ask, or decide and say so

A design question belongs to the person when it changes the identity of the
product or cannot be undone cheaply: brand colours, the name, which audience
comes first, whether to break an existing convention. Ask those with two or
three concrete options and your recommendation. Everything else (a spacing
value, a label, the order of two fields) decide yourself and state the choice
in the report. A barrage of small questions costs the person more than a
reasonable default they can change.

## 3. Ground it in the subject

Write one line for each:

- **Subject**: what the product is about, in concrete terms ("invoices for
  freelance translators", not "a SaaS tool").
- **Audience**: who uses it, how often, on what device, with what expertise.
  A tool used eight hours a day earns density and shortcuts; a tool used once
  a year earns explanation and forgiveness.
- **Job of this screen**: one sentence. If you need two, it may be two screens.
- **Success**: what the person can do afterwards that they could not before.

Distinctive choices come from the subject's world: its materials, units,
documents, instruments, vocabulary. A brewery's site can borrow the gravity
readings and batch labels of brewing; a log viewer can borrow the severity
conventions its users already know. Put at least one real detail of the
subject into the content (its units, its document conventions, its terms of
art), not as ornament.

Use real content from the start. Lorem ipsum hides the real problems: the
85-character product name, the empty description, the price with four digits.

## 4. The design plan

For anything bigger than a component, write a compact plan before code. Put
it where it will be used: as the token block at the top of the stylesheet or
theme module, with the layout idea as a comment.

- **Colour**: 4–6 named tokens (background, surface, text, muted text, accent,
  plus semantic success/warning/danger if the UI shows state). Name by role
  (`--surface`, `--text-muted`), never by value (`--grey-2`).
- **Type**: the families and their roles (display, body, mono), a scale of
  5–7 sizes, the weights in use.
- **Space**: a scale (4, 8, 12, 16, 24, 32, 48, 64 px or the project's).
- **Shape**: radius and elevation by role (the dialog is raised, the row is
  not), not one value for everything.
- **Layout**: a one-line concept and an ASCII wireframe of the main screen at
  the widest and the narrowest size.
- **States**: the list from the next section.
- **Principle**: one sentence on what makes this one specific ("ledger-like:
  aligned figures, hairline rules only between groups, red reserved for
  overdue").

Then **review the plan against the request**: for each line, would you have
written the same for any similar request? If yes, replace it with a choice
this subject earns, and note what you changed. This step is what keeps work
from looking generated.

## 5. The state inventory

List the states of every screen and component before building, and build
each one:

- **First run**: nothing configured, no data, no history.
- **Empty**: configured but no items. Say what will appear and give the action
  that creates the first one. An empty state is a beginning, not an error.
- **Loading**: skeletons that match the final layout for content, a spinner
  only for short unknown waits, progress for long ones (timings in
  `references/interaction.md`).
- **Partial**: some data arrived, some failed or is still loading.
- **Ideal**: the one everyone designs.
- **Too much**: 10 000 rows, a name of 90 characters, a word with no break
  point, 40 tags, a number with 12 digits, a pasted 4 MB text.
- **Error**: per field, per section, whole page; recoverable and not.
- **Offline / timeout / rate limited.**
- **No permission**: say who can grant it, do not just hide the feature.
- **Success**: confirm what happened, show the result, offer the next step.
- **Destructive pending**: the confirmation or undo window.
- **Disabled**: say why something is unavailable (a tooltip or helper text),
  or do not show it.

## 6. Critique: heuristic evaluation

Use Nielsen's ten heuristics as the checklist when reviewing any UI, yours or
existing. For each problem record: where, which heuristic, severity, and a
concrete fix.

1. **Visibility of system status**: the person always knows what is happening
   (loading, saved, syncing, failed), within a reasonable time.
2. **Match with the real world**: the person's words and concepts, in a
   natural order; no internal names, error codes or IDs as primary text.
3. **User control and freedom**: an obvious way out of every state: undo,
   cancel, back, close, Escape.
4. **Consistency and standards**: the same word, icon and position mean the
   same thing everywhere; platform conventions are followed.
5. **Error prevention**: constraints, good defaults, confirmation for the
   irreversible, warnings before the mistake rather than after.
6. **Recognition rather than recall**: options visible, recent items offered,
   values carried between steps, no need to remember an ID from a previous
   screen.
7. **Flexibility and efficiency**: shortcuts, bulk actions, a command palette
   and saved filters for frequent users, invisible to novices.
8. **Aesthetic and minimalist design**: every element competes for attention;
   remove what does not serve the screen's job.
9. **Help users recognise, diagnose and recover from errors**: plain language,
   the precise problem, a constructive next step.
10. **Help and documentation**: searchable, task-oriented, close to where it
    is needed; the best help is the one that is not needed.

Severity: **4** blocks the task or loses data; **3** causes errors or large
delays for many people; **2** slows people down or confuses some; **1**
cosmetic. Fix 4 and 3 before anything decorative.

## 7. Laws worth keeping in mind

- **Fitts**: time to hit a target grows with distance and shrinks with size.
  Put frequent actions near where the pointer already is; make them big; use
  screen edges and corners for menus.
- **Hick**: decision time grows with the number of choices. Fewer options per
  step, a recommended default, progressive disclosure for the rest.
- **Jakob**: people expect your product to work like the others they use.
  Innovate in what matters to the subject, not in where the search box goes.
- **Miller / chunking**: working memory is small. Group long numbers (keys,
  phone numbers), chunk settings into sections, show at most about 7 items
  without structure.
- **Doherty threshold**: under about 400 ms per interaction, people stay in
  flow. Above it, they start waiting.
- **Tesler**: complexity that cannot be removed has to live somewhere; put it
  in the system, not on the person.
- **Postel**: accept input liberally (formats, spaces, case), produce output
  strictly and consistently.
- **Peak-end**: people remember the worst moment and the last moment. Make the
  end of a flow (the install finishing, the order confirmed) clear and good.
- **Von Restorff**: the one thing that differs is remembered. Use it for the
  primary action, and only for that.
- **Goal gradient**: people speed up near the end. Show progress in multi-step
  flows; start the bar with a step already done when that is true.
- **Zeigarnik**: unfinished tasks stay on the mind. A visible "2 of 5 done"
  invites completion; a hidden one is forgotten.
- **Aesthetic-usability**: attractive interfaces are perceived as easier, which
  also hides their usability problems from tests. Test with tasks, not opinions.
- **Gestalt**: proximity, similarity, common region and connection group
  things. Space is the cheapest grouping device; use it before borders.

## 8. Auditing a flow by counting actions

The most useful single measurement for a flow is the number of actions it
takes and where people get lost. Kvist's own onboarding was redesigned this
way.

1. Define the tasks as the person would say them ("connect my key and send a
   first request", "change the model mid-session").
2. Set up a clean stand: a fresh home directory or profile, a temporary
   workspace, fake or test credentials, no network to real services where you
   can avoid it.
3. Walk each task and count **actions**: one click, one filled field, one key
   press, one typed command. Opening a dropdown and choosing an item is two.
   Waiting is not an action but note how long.
4. For each step, capture the screen (the browser skill's `screenshot` with a
   question such as "What would a first-time visitor do next on this
   screen, and is that action visible without scrolling?"; for a terminal,
   the harness in `terminal.md`, whose text you read yourself) and ask: would
   a person without the documentation see the next step within five seconds?
   Mark every step where the answer is no. This is a judgement, so say it is
   one, and keep the captures for the report.
5. Do it at two sizes (for the web, `emulate` 1440×900 and 390×844; for a
   terminal, 120×40 and 80×24).
6. Set a target for each task (the minimum the design could need), change the
   design, and walk the same tasks again on a fresh stand. Report before and
   after in a table.

Report the result as numbers and screenshots, not adjectives:

| Task | Before | After | What changed |
| --- | --- | --- | --- |
| Connect a provider | 6 | 4 | key validated on paste; model list loads itself |

## 9. Critique of the visual layer

Go through these after the heuristics, on the rendered result:

- **Squint test**: blur your eyes (or the screenshot). The most important thing
  should still be the most visible. If three things compete, demote two.
- **Hierarchy**: one primary action per view. Secondary actions look secondary
  (outline or text buttons), destructive ones look different and are not next
  to the primary.
- **Alignment**: every edge lines up with something. Count the distinct left
  edges on the screen; fewer is calmer.
- **Spacing rhythm**: values come from the scale; space inside a group is
  smaller than space between groups.
- **Repetition**: cards, rows and badges of the same kind have the same
  padding, baseline and placement of recurring elements.
- **Density**: matches use. A monitoring console can be dense; a signup form
  should breathe.
- **Colour budget**: neutrals carry the layout, one accent marks interaction,
  semantic colours mark state. More accents dilute all of them.
- **Type**: no more than two families, sizes from the scale, no orphaned
  single words in headings (`text-wrap: balance`), figures in tables aligned
  (`tabular-nums`).
- **Remove one thing**: before finishing, take away one decoration, border or
  shadow that does not carry information.

## 10. Reporting design work

The person reads the report to decide whether to trust and keep the change.
Say:

- what changed for the person using it (two or three sentences);
- which states, sizes, themes and inputs you checked, and how (screenshots,
  pty run, keyboard walk);
- what you could not check, plainly;
- decisions you made that they may want to change (with the one place to
  change them, such as the token block);
- for an audit: the table of tasks and counts, and the problems by severity.

Do not describe hex values and font names unless asked; describe the effect.
