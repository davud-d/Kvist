# Kvist Pixel on the cloud

*(Русская версия: [README.ru.md](README.ru.md))*

The pixel-game window of [Kvist Pixel (Tauri)](../../frontends/gui/tauri/README.md), copied here and changed
only where the cloud has different data. It is plain files, no bundler: `cloud/frontend/build.mjs` copies it to
`dist/pixel/` and the server serves it at **`/pixel/`** (the old pages stay as they are; the header has a "Pixel"
link). Open a task directly with `/pixel/#/tasks/<id>`.

## What is the same

The reducer, feed, dialogs, themes, squirrel, motion and markdown/KaTeX are the desktop app's own. They work
unchanged because the cloud relays Kvist's events as they are (`cloud/docs/ARCHITECTURE.md`, "A task").

## What is cloud data now

| Desktop app | Here |
| --- | --- |
| Project folders in the rail | **Repositories** (and the empty folder); "add repository" takes an https link |
| A session per folder | **A task**; the rail lists them, newest first, grouped by repository |
| Window controls, resize edges | Gone (it is a web page); a language switch instead |
| Core's models, role, effort, style | The person's **model key** (provider + key, checked on save) |
| Settings: sessions, revert, export… | **Account**: key, plan and billing, sign out; and how the page looks |
| Inspector: helpers, screen, jobs, context | **Hours**, **Server**, **Screen**, files/context, helpers, jobs |
| — | Title strip: **hours left** and the **server's state** (asleep, ready, working…) |

* **Hours** shows what is left of the plan, what this task used and the ledger. Buying hours opens the plan page.
* **Server** shows the machine (asleep / ready / working, vCPU, RAM, disk), wakes or sleeps it and exports an
  archive of its files.
* A task has **one goal**: once it is sent, the composer gives way to a line, and Enter starts another task (or
  continues a paused one). Pause and stop are in the task header; the agent's questions are the usual dialogs.
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
