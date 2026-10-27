# An instructions-only skill

No code, no permissions, no sandbox: it works on Linux, macOS and Windows and is attached
without the runner. It is the right shape for a procedure, a style guide, domain facts or a
checklist.

## Files

```
kvist-skills/<id>/
  manifest.json
  SKILL.md
  references/<topic>.md      optional, read one at a time on demand
```

Write them with `write_file`. Do not call `scaffold`: it makes a tools skill, and you cannot
delete its `plugin.py` and tests afterwards.

## manifest.json

```json
{
  "manifest_version": 1,
  "id": "release_notes",
  "version": "0.1.0",
  "plugin_api": 1,
  "kind": "instructions",
  "name": {"en": "Release notes", "ru": "Заметки о релизе"},
  "summary": {
    "en": "Writes release notes from git history in this project's format.",
    "ru": "Пишет заметки о релизе по истории git в формате этого проекта."
  },
  "instructions": "SKILL.md",
  "platforms": ["linux", "darwin", "win32"],
  "source": {"origin": "builder"},
  "capabilities": {"required": []}
}
```

- Unknown top-level keys are refused. An instructions skill declares no `tools`,
  `entrypoint` or dependencies.
- `summary` is at most 300 characters and is the only thing in the catalog. Say what the
  skill does and when it applies; "helps with docs" attaches at the wrong times or never.
- `ru` text is optional but worth giving when the person writes Russian.

## Writing SKILL.md

A good page has this shape, in this order:

1. **When to use it**, in two or three lines, including when *not* to.
2. **The procedure**, as the steps a careful person would take, each with the reason.
3. **The quality bar**: what a finished result looks like and how to check it.
4. **Pointers** to reference pages, each with the situation that calls for it.

Rules that work:

- Give the reason beside every rule ("run the failing test first, because a fix you
  cannot see fail proves nothing"). The model generalizes from reasons and over-applies bare orders.
- Prefer a concrete example over an adjective. Show the good output once.
- State what the skill cannot do, so the model does not pretend.
- Stay under a few hundred lines. Anything a task needs only sometimes becomes a reference page.
- No orders aimed at the system ("ignore previous rules", "never ask the person"): they are
  ignored and make the package look hostile to the person who reads it at install.

## Reference pages

`references/<topic>.md`, at most 64 KiB each, UTF-8. The attach answer lists them and the
model reads one with `skills` op `read`. `check` warns about a page `SKILL.md` never names
and fails on a named page that is missing. Name a page by its path:
"For error messages read `references/errors.md`."

## Test it before you offer it

There is no unit test for prose. Do this instead: attach nothing, take one real task the
skill is for, follow your own `SKILL.md` literally, and note every place where you needed
something it did not say. Add it, then check. If you cannot find a real task, the skill is
probably not needed.
