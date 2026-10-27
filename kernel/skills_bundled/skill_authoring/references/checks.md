# Reading what `skills check` reports

`check` runs on a copy of the draft and never executes it on the host. It is separate from
your own tests and you cannot configure it. A report has findings with a severity: an
`error` blocks install, a `warning` does not but is shown to the person.

## What it checks

- **manifest**: valid JSON, known keys only, id and tool names, `MAJOR.MINOR.PATCH`,
  `plugin_api: 1`, limits within bounds, a tools skill declares at least one tool and an
  instructions skill declares none. A refusal names the field; fix that field.
- **instructions**: `SKILL.md` exists, is UTF-8 and at most 64 KiB.
- **permissions_listed**: shows what the skill will be granted.
- **references**: every `references/*.md` is UTF-8 and at most 64 KiB; a page `SKILL.md`
  names but the package lacks is an error; a page never named is a warning ("the model will
  not know when to read it"). Name it by its path in `SKILL.md`.
- **Python** (tools skills): the code is compiled, never executed; every `context.<group>.<method>`
  call must be covered by a declared capability.
- **sandbox run**: `describe()` matches the manifest; every tool answers its sample
  arguments within its time and size limits and matches its `result` schema; a cancel
  ends a call; an undeclared request is refused; nothing keeps running when the runner stops.
- **author tests**: your `tests/test_*.py` under `unittest`, in the sandbox, without network.
- **similar skills**: installed skills that look alike. Take it seriously: use the existing
  one or say why the new one is different.

## Common failures

| Report | Cause | Fix |
| --- | --- | --- |
| manifest refuses an unknown key | a typo or a field from another format | remove it; only the keys in `docs/SKILLS.md` exist |
| "instructions-only skill declares no tools" | `kind: instructions` with a `tools` list | remove the tools, or make it a tools skill |
| call needs a capability not declared | code uses `context.files.write` with only `files.read` | declare it with a reason, or stop using it |
| tool fails its sample call | `contract.json` sample invalid, or a path outside the scope | fix the sample; make the tool return a clear error for bad input |
| result does not match schema | a field is missing or has another type | fix the code or loosen `result` |
| module fails to load | `@dataclass` with `from __future__ import annotations`, or a dependency not pinned | use a plain class; pin exactly |
| cleanup finding | a thread or process left running | stop it in a `finally`, honour `context.runtime.check()` |
| changed after check, refused at install | you edited the draft | check again, then install |

Passing checks show that the contract, permissions and cleanup held in the cases tried.
They do not show the skill is useful or harmless; that is what your own tests and the
person's review at install are for. After an install the skill is available only after Kvist is restarted.
