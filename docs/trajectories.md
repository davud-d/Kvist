# Trajectories (training data)

`KVIST_TRAJECTORY_DIR=<dir>` makes Kvist record every run as training data: each model
call exactly as it was sent and received, with its reasoning, and the result of every
tool call. It is off by default. The code is `kernel/agent/trajectory.py`.

```bash
export KVIST_TRAJECTORY_DIR=~/kvist-trajectories
.venv/bin/python -m kernel --workspace <path>     # or scripts/run_task.py, or any frontend
```

The Harbor adapter (`scripts/bench/kvist_harbor.py`) turns it on by itself: each trial
gets `agent/trajectories/` beside the verifier's reward, which is what tells a
successful run from a failed one.

Why the data exists: [models.md](models.md).

## Rating a session: `/rate`

`/rate` opens "Rate this session as training data" with two choices. **Good example**
moves the session's runs into `good/`, **Bad example** into `bad/`; leaving the dialog
moves nothing. The files only move inside the trajectory folder; nothing is sent.
Rating again moves them to the other side, and a session continued after its rating
is rated again with its new runs, which join the files already there.

```
<dir>/
  <session folder>/<run_id>.jsonl          not rated yet
  good/<session folder>/<run_id>.jsonl     reference examples
  bad/<session folder>/<run_id>.jsonl      bad examples
```

The session folder is `<hash of the workspace path>-<session name>`: the same session
name occurs in many workspaces.

## Format

One JSON-lines file per run, private to the user (mode 0600). A resumed run appends to
its own file, so it can hold more than one `run_start`.

| `type` | What it holds |
|---|---|
| `run_start` | `goal`, `session`, `provider`, `model`, `role`, `reasoning_effort`, `response_language`, `start_step` |
| `message` | one distinct chat message (or tool list) under `id`, written the first time it is sent |
| `call` | one model call that got an answer: `step`, `kind` (`step` for the action loop; reports and other calls have their own kinds), `protocol` (`native` or `text`), `model`, `messages` (ids, in the order sent), `tools` (id or null), `response`, `usage`, `seconds` |
| `results` | the calls of one response: `call_id`, `name`, `arguments`, `valid` (passed validation), `result` (the text the model was given back) |
| `run_end` | `outcome` (`done`, `incomplete`, `closed`, `stopped`, `paused`, `error`), `final_message`, `steps`, `files_changed`, `finished_incomplete` |

`response` is `{content, reasoning}` for the text protocol (the content is the raw
text with its `<tool_call>` blocks) and `{content, reasoning, tool_calls, text_after_calls,
cut_off}` for native tools, where `tool_calls[].arguments` is the raw JSON string the
model wrote.

A native step sends the whole conversation again and a text step sends the same system
prompt again, so messages are stored once under a hash and calls refer to them by id;
a file grows with the number of steps, not with its square. `read_trajectory(path)`
returns the calls with their prompts rebuilt in full:

```python
from kernel.agent.trajectory import read_trajectory

for record in read_trajectory(path):
    if record["type"] == "call" and record["kind"] == "step":
        prompt, answer = record["messages"], record["response"]
```

## What to know before using the data

- **Secrets** pass through the same redaction as the logs, but the prompts hold the
  workspace's code and paths. Look at the data before it leaves the machine.
- **A call that failed** (an error, a retry, a cancelled stream) is not recorded; the
  retry that got an answer is.
- **Cached answers** (`cache_enabled`) are not recorded: they were not a model call.
- **Not every call is a step.** Filter on `kind == "step"` for the action loop.
- **Usage** on the text protocol is estimated locally (`source: "estimated"`).
