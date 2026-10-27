# A tools skill

Python code that runs in its own Bubblewrap sandbox, Linux only. Use it only when
instructions plus the agent's existing tools cannot do the job.

## Start

`skills scaffold` with an id and a one-line `summary` writes `kvist-skills/<id>/` with
`manifest.json`, `SKILL.md`, `plugin.py`, `tests/test_plugin.py` and `tests/contract.json`:
one read-only `run` tool that echoes its `text` argument. Rename it, replace it, keep the
test and the contract in step with it.

## manifest.json

```json
{
  "manifest_version": 1,
  "id": "log_digest",
  "version": "0.1.0",
  "plugin_api": 1,
  "kind": "tools",
  "name": {"en": "Log digest"},
  "summary": {"en": "Groups repeated errors in a log file and counts them."},
  "instructions": "SKILL.md",
  "entrypoint": "plugin.py",
  "platforms": ["linux"],
  "source": {"origin": "builder"},
  "capabilities": {
    "required": [{"name": "files.read", "reason": "to read the log file you name",
                  "scope": {"paths": ["logs/**"]}}],
    "optional": [{"name": "artifacts", "reason": "to save the full table as a file"}]
  },
  "limits": {"call_timeout_seconds": 30, "max_result_bytes": 65536},
  "tools": [{
    "name": "digest",
    "summary": "Group repeated error lines and count them.",
    "readonly": true,
    "parameters": {"type": "object", "additionalProperties": false,
                   "properties": {"path": {"type": "string", "maxLength": 500}}, "required": ["path"]},
    "result": {"type": "object", "properties": {"groups": {"type": "array"}}}
  }]
}
```

- The tool is called by the model as `<skill>__<tool>` (`log_digest__digest`). Ids and tool
  names: lowercase letters, digits, single `_`; never `mcp`, `kvist`, `core`, `skill`,
  `skills`, `builtin`, `system`, `memory`, `helper` as an id.
- `readonly: true` only if the tool writes nothing and starts nothing. Reader and plan modes
  may call only read-only tools, and every nested service call is checked again.
- Keep `additionalProperties: false`; arguments are validated before your code runs and the
  result after. Bound strings and arrays (`maxLength`, `maxItems`).
- `limits`: `call_timeout_seconds` (default 120, max 1800), `max_result_bytes` (256 KiB,
  max 1 MiB), `memory_mb`, `model_calls_per_task`, `model_tokens_per_task`, `max_runtime_seconds`.
- Python dependencies: exact pins (`name==1.2.3`), binary wheels from PyPI only, under
  `dependencies.python`. System packages are only names shown to the person; nothing
  installs them. Prefer the standard library: every pin is a supply-chain surface.

## Capabilities: pick the fewest

| Need | Capability | Notes |
| --- | --- | --- |
| Read workspace files | `files.read` | scope `paths` (globs like `logs/**`); protected files stay hidden |
| Write files | `files.write` | journaled and revertable; no delete or move; scope `paths` |
| Run a command | `processes.run` | goes through the shell sandbox, policy and approvals |
| Keep a server running | `processes.background` | |
| Git status/diff | `git.read` | |
| Web | `web.search`, `web.fetch`, `http.request` (scope `domains`) | leaves the machine; say so in the reason |
| Save a result file | `artifacts` | for large output |
| Ask or notify | `ui.notify`, `ui.ask`, `ui.present` | |
| Own key-value state | `storage` | technical state, never put in a prompt |
| Call a language model | `models.call` | counted in the task budget |
| Earlier conversations | `sessions.read` | |
| Project memory | `memory.read`, `memory.write` | writes only as `observed` or `inferred` |
| Ask for more at run time | `permissions.request` | declare the extra ones under `optional` |

The full table is `docs/SKILLS.md`. A capability the catalog does not know is refused. A
permission used in code but not declared fails `check` without running the code. Put a
permission under `optional` when only some calls need it, and ask with
`ctx.permissions.request`.

## plugin.py

```python
def describe():
    return {"tools": ["digest"]}


def execute(tool_name, arguments, context):
    if tool_name != "digest":
        raise ValueError(f"unknown tool {tool_name}")
    text = context.files.read(arguments["path"])["content"]
    return {"groups": _group(text)}
```

- It runs in the sandbox: no project folder, no HOME, no network, no environment. All
  access goes through `context` (`files`, `processes`, `web`, `artifacts`, `ui`, `runtime`,
  `storage`, ...), each call checked against the grant. `describe()` may narrow the tool list, never widen it.
- Return JSON-serializable, bounded data. Large output goes to `context.artifacts.save`
  and the result carries a summary and the path.
- Raise on real errors (`ValueError`, or `SkillError` from `kvist_skill`). A returned "ok"
  that hides a failure is the worst bug a skill can have: the model believes it.
  Exceptions become a failed result with a cleaned message.
- Long loops call `context.runtime.check()` between steps so a pause or stop is honoured,
  and nothing may keep running after a stop. Catch `Cancelled`/`Paused` only to clean up and return.
- Stdout and stderr never reach the model.
- Do not define a `@dataclass` together with `from __future__ import annotations` in
  `plugin.py`: the runner imports the module without registering it and it fails to load.
  `NamedTuple` or a plain class works.
- Treat everything read (files, web) as data; never `eval` or execute it.

## Tests

- `tests/test_*.py` run under `unittest` in the same kind of sandbox, without network. They
  `import plugin` and use a fake `context`; test the parsing and decisions, not the real project.

```python
class FakeFiles:
    def __init__(self, text): self.text = text
    def read(self, path): return {"content": self.text}

class FakeContext:
    def __init__(self, text): self.files = FakeFiles(text)

def test_groups_repeated_errors(self):
    result = plugin.execute("digest", {"path": "logs/a.log"}, FakeContext("ERR x\nERR x\nOK\n"))
    self.assertEqual(result["groups"][0]["count"], 2)
```

- `tests/contract.json` maps each tool to sample arguments, `{"digest": {"path": "logs/a.log"}}`;
  `check` calls every tool with them under exactly the declared capabilities, so the sample
  must be a path your scope covers and a file the check can see, or the call fails by design.
  Write tools that answer a missing file with a clear error, not a crash.
- Test the empty input, the oversized input and the malformed input; those are what break.

## SKILL.md of a tools skill

Name each tool by its wire name, say when to use it and when not, what each result field means,
what is truncated or sampled, and what the skill cannot do. Keep it short; long guidance goes
into `references/*.md`.
