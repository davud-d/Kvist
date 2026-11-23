# Kvist Pixel on the cloud

*(Русская версия: [README.ru.md](README.ru.md))*

The pixel-game window of [Kvist Pixel (Tauri)](../../frontends/gui/tauri/README.md), copied here and changed
only where the cloud has different data. It is plain files, no bundler: `cloud/frontend/build.mjs` copies it to
`dist/pixel/` and the server serves it at **`/workspace/`** (an old `/pixel/` address redirects there; the old pages stay as they are; the header has a "Workspace"
link). Every session has its own address, `/workspace/s/<id>` (an opaque id), so a link opens it; only its owner can open it.

## What is the same

The reducer, feed, dialogs and markdown/KaTeX are the desktop app's own. They work unchanged because the cloud
relays Kvist's events as they are (`cloud/docs/ARCHITECTURE.md`, "A task").

## How the workspace is laid out

The cloud is a work tool for people who pay to save time (accountants, doctors, founders, developers) and who
may have many agents running at once without watching each. The page is built around three things they do:
start work, see what is happening, come back to earlier work. There are no standing side columns.

* **The rail** (`js/views/rail.js`) is the only thing always on screen: Home, Tasks (search, Ctrl+K), and at the
  bottom Server and Settings, each with a word under its icon. The Home button carries one dot: **yellow** when a
  task waits for the person, **blue** when one finished since they last looked (`js/attention.js`; "looked" is
  remembered in this browser, and the first visit sets a baseline so old tasks are not news).
* **Home** (`js/views/home.js`): one question and one field to hand a task over, with the project it goes to and
  the provider and model it runs on inside the field. Under it only what needs a look, in that order: *Waiting for
  you*, *While you were away*, *In progress*, and a few *Recent* tasks. A list with nothing in it is not drawn.
* **The model and effort buttons** sit in the home field and under an open task's field (`js/views/run-picks.js`):
  the key and the effort are the account's, and a task takes them from its next message; in a task the menus open
  upwards, and Esc closes the menu rather than pausing the agent.
* **The model picker** (`js/views/model-picker.js`): a search over providers and models, then providers as parent
  rows with their marks. The connected one comes first and lists the models its saved key can use
  (`GET /api/model-catalog/saved`) plus a field to type one; picking one is checked like a new key
  (`PUT /api/model`). Another provider opens into "Connect key", because the account keeps one key. DeepSeek,
  OpenAI and Anthropic have their models chosen by Kvist, and the picker says so.
* **Model names** (`js/models.js`): a provider's id is read as a name by rules, not by a table of every model
  (`claude-opus-5-5` → "Claude Opus 5.5", `deepseek-v4.1-flash-1017` → "DeepSeek V4.1 Flash"), so tomorrow's model
  reads right too. The name is only for reading: the id is what is saved and sent, and the menu shows it under the
  name. For known families (Opus, Sonnet, Haiku, Fable, Astra, Sol, Luna, Carbon, Neon, Argon, Hydrogen, DeepSeek,
  GLM) the home question names the model ("What should Opus take on?") and a line under it jokes about the irony
  of the task and the model mismatching, with a tip which one fits (never about the person who chose it); each
  family has two or three lines, one picked per visit; no prices, they change every month.
* **Effort** (`js/views/effort.js`), next to the model in the field and in the settings: a track with a stop per
  level (from "Off" to "Max"), the level in words above it with the model it applies to (from the field that line
  opens the model picker), and a "back to medium" button. It is dragged or moved with the arrow keys; every step,
  a drag's too, glides to its stop in 160 ms. Several quick steps are saved with one request and no toast: the
  dial already shows the level.
* **A task in focus** (`js/views/chat.js`) takes the whole stage. Its header says the name and, in one line, the
  project, the state and the hours it cost; under it the **course of work** (the conversation; what
  the agent did between two messages folds into one line, `js/feed.js`; thoughts stand in the conversation on
  their own, as in Kvist Glass: while the model thinks, a window of its latest lines fading at the top with a
  clock, then one line, "Thought for 7 s", that opens the whole text on a click; a group of calls folds as in Glass too, `js/folds.js`: open
  while it runs, held for two seconds once something follows it, then folded in 320 ms). Inside one turn the
  thoughts, calls and words stand close; the space between turns is wider. When the agent asks something, the
  question stands where the message field was.
* **Notifications** (`js/notify.js`): the page asks the browser for them when the person hands over a task (a
  browser asks only in answer to a click or a key). A task that finishes, fails or asks something while the page
  is in another tab or window shows a system notification; a click on it opens the task. A pause or a stop the
  person made is not told.
* **The orb** (`js/views/orb.js`) stands where the agent is now, in place of the caret. While the model speaks it is
  a blue dot breathing at the end of its words; otherwise its own line at the end of the feed shows what it is about,
  from the moment a call is planned: a faint brain breathing while it thinks; a terminal with a blinking cursor; a
  book turning a page while reading; adding is a line written at the end of an open book; changing is a line rubbed
  out by an eraser and written again; a folder, a magnifier, a globe; a skill is a book drawn from a library shelf;
  helpers are dots circling. The loops run on the page's clock, so a repaint does not restart them.
* **Commands**: "/" opens a hint (`js/views/commands.js`) with Kvist's own list (its `manifest`), less what the
  cloud takes away (`cloud/docs/ARCHITECTURE.md`, "Kvist's slash commands"). Kvist's commands go to the task and
  their answer stands in the conversation; the page runs `/provider`, `/models`, `/change-api`, `/effort`, `/rename`
  and `/new` itself. Home offers only what needs no task. Shift+Esc from anywhere puts the cursor in the
  field: the home one or the open task's.
* **The side sheet** (`js/views/sheet.js`) opens on request and closes with Esc: the server and the hours (from the
  rail), the agent's screen, its helpers, its background commands (from the task header, shown only when the task
  has them). The column moves over for it instead of being covered.

## How it looks

* **Themes:** light, dark, or the system's (`css/theme.css`, every colour is a `light-dark()` pair; the settings
  switch them, the new colours open as a circle from the button). Old saved palettes map onto the new themes.
* **Colour has roles:** green is the brand and the main action only; the chosen thing is a neutral fill
  everywhere; blue and yellow are the two signals above; red is an error. Surfaces have levels (the rail below
  the stage, floating things above it) instead of borders around everything.
* **Type by role:** the home question, a task's name, reading text, interface text, secondary lines (never below
  13px), times. Golos Text for the interface, JetBrains Mono for code.
* **Shape by role:** the message field is the one big rounded object; rows are square until hovered or chosen;
  floating things are medium; a circle is always a signal. Line icons (`js/dom.js`).
* **Motion** (`js/motion.js`): short, ease-out, never a bounce; exits faster than entrances; hover effects only
  where a mouse can hover; "reduce motion" is respected.
* **A file being written** streams into its card line by line, coloured by its language (`js/highlight.js`),
  and folds away two seconds after the call ends, as in Kvist Glass.

## What is cloud data now

| Desktop app | Here |
| --- | --- |
| Project folders in the rail | **Projects**: picked inside the home field; "New project" makes a folder on your server, either a clone of one of your GitHub repositories or just a name (names never repeat) |
| A session per folder | **A task** (a session) in a project; home and the task search list them |
| Window controls, resize edges | Gone (it is a web page) |
| Core's models, role, effort, style | The person's **model key** (provider + key, checked on save) |
| Settings: sessions, revert, export… | **Account**: key (for "Other": extra headers and a streaming switch, on by default, which is what shows a file while the model writes it), renaming a task (`/rename`), how hard the model thinks (`/effort`, kept on the server, applied from the next message), plan and billing, sign out; and how the page looks |
| Inspector: helpers, screen, jobs, context | The side sheet, on request: **Server and hours**, **Screen**, helpers, background commands, a file's changes |

* **Server and hours** shows the machine (its state with the one action that fits it: wake when asleep, sleep when
  idle; processor, memory, disk), exports an archive of its files, and the agent's working hours left in the
  plan with the ledger. Buying hours opens the plan page.
* A task is a **session**: you keep writing to it. The first message starts the agent on your server; the next ones
  go into the same conversation, which the agent remembers (the cloud starts Kvist again and switches back into
  it if it had stopped; a message sent while the agent works queues behind its turn). The conversation is stored,
  so it is all there after a reload. Pause and stop are in the task header, a paused task has a continue button.
* No attachments: a cloud task takes words only.

## The agent's screen is private

The **Screen** sheet shows the agent's browser and desktop. What the agent does there is as private as its files:
the machine's address and token never leave it, pictures are pulled only while this sheet is open, they are kept in
memory only, and the server hands them to the signed-in owner of the task and nobody else (another account, an
admin and a signed-out visitor all get "not found"). Details and tests: `cloud/docs/ARCHITECTURE.md`,
"The agent's screen (private)".

## Layout

`js/cloud.js` the account (hours, server, key, tasks) and the page's pure helpers · `js/bridge.js` the only place
that talks to the cloud (HTTP and the task's WebSocket) · `js/attention.js`, `js/feed.js` what needs a look and
how a task's items are grouped (pure) · `js/panels/` what the side sheet shows · `css/cloud.css`
what the cloud adds · `tests/` the pure logic (`node --test "tests/*.test.mjs"`).

Fonts and KaTeX: see [THIRD_PARTY.md](THIRD_PARTY.md).
