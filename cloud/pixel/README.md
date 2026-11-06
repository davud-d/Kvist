# Kvist Pixel on the cloud

*(Русская версия: [README.ru.md](README.ru.md))*

The pixel-game window of [Kvist Pixel (Tauri)](../../frontends/gui/tauri/README.md), copied here and changed
only where the cloud has different data. It is plain files, no bundler: `cloud/frontend/build.mjs` copies it to
`dist/pixel/` and the server serves it at **`/pixel/`** (the old pages stay as they are; the header has a "Workspace"
link). Open a task directly with `/pixel/#/tasks/<id>`.

## What is the same

The reducer, feed, dialogs, themes, squirrel, motion and markdown/KaTeX are the desktop app's own. They work
unchanged because the cloud relays Kvist's events as they are (`cloud/docs/ARCHITECTURE.md`, "A task").

## What is cloud data now

| Desktop app | Here |
| --- | --- |
| Project folders in the rail | **Projects**: "Add project" makes a folder on your server, either a clone of one of your GitHub repositories or just a name (names never repeat) |
| A session per folder | **A task** (a session) in a project; the rail lists them, newest first |
| Window controls, resize edges | Gone (it is a web page); a language switch instead |
| Core's models, role, effort, style | The person's **model key** (provider + key, checked on save) |
| Settings: sessions, revert, export… | **Account**: key, plan and billing, sign out; and how the page looks |
| Inspector: helpers, screen, jobs, context | **Hours**, **Server**, **Screen**, files/context, helpers, jobs |
| — | Title strip: **hours left** and the **server's state** (asleep, ready, working…) |

* **Hours** shows what is left of the plan, what this task used and the ledger. Buying hours opens the plan page.
* **Server** shows the machine (asleep / ready / working, vCPU, RAM, disk), wakes or sleeps it and exports an
  archive of its files.
* A task is a **session**: you keep writing to it. The first message starts the agent on your server; the next ones
  go into the same conversation, which the agent remembers (the cloud starts Kvist again and switches back into
  it if it had stopped; a message sent while the agent works queues behind its turn). The conversation is stored,
  so it is all there after a reload. Pause and stop are in the task header, a paused task has a continue button,
  the agent's questions are the usual dialogs.
* No attachments: a cloud task takes words only.

## The agent's screen is private

The **Screen** tab shows the agent's browser and desktop. What the agent does there is as private as its files:
the machine's address and token never leave it, pictures are pulled only while this tab is open, they are kept in
memory only, and the server hands them to the signed-in owner of the task and nobody else (another account, an
admin and a signed-out visitor all get "not found"). Details and tests: `cloud/docs/ARCHITECTURE.md`,
"The agent's screen (private)".

## Layout

`js/cloud.js` the account (hours, server, key, tasks) and the page's pure helpers · `js/bridge.js` the only place
that talks to the cloud (HTTP and the task's WebSocket) · `js/panels/` the inspector's tabs · `css/cloud.css`
what the cloud adds · `tests/` the pure logic (`node --test "tests/*.test.mjs"`).

Fonts and KaTeX are the desktop app's: see [THIRD_PARTY.md](THIRD_PARTY.md).
