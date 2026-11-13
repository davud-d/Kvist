# Benchmarks

Kvist runs on [Terminal-Bench](https://www.tbench.ai/) and the other datasets of the
[Harbor](https://harborframework.com/) harness through `scripts/bench/kvist_harbor.py`.
Harbor starts each task in its own container, installs Kvist there, gives it the task's
instruction and then runs the task's tests.

## How Kvist runs inside a task

- **Code.** The adapter packs `kernel/`, the two frontends the core imports
  (`frontends/tui/classic`, `frontends/zed`), `scripts/run_task.py` and
  `requirements.txt` from the checkout's **HEAD commit** (`git archive`), never from
  the working tree. Commit before a run; the trial records the commit as the agent
  version.
- **Python.** uv installs its own Python 3.13 and the core's packages under
  `/opt/kvist`. The task's Python, packages and HOME stay as the task built them.
- **Sandbox.** `run_task.py --container` sets `AGENT_SANDBOX_MODE=container`: the task's
  container is the sandbox, so commands run unwrapped with the task's HOME and the
  container's network, the rules that protect a person's machine (`bash -c`, `rm`,
  running workspace programs, the 2000-character command cap) are lifted, and every
  question gets yes. Each yes goes to stderr, so the log shows what was let through.
  Outside a Docker or Podman container both the mode and `--container` are refused.
  Secrets are still kept out of the commands' environment.
- **Workspace.** The container's working directory. Kvist does not create `KVIST.md`
  there or touch `.gitignore`; its own state (`.kvist/`) is moved to the agent logs
  before the tests run.
- **Logs.** `agent/kvist.txt` (the whole run, step by step) and `agent/kvist-state/`
  (session, change journal) in the trial directory. Input and output tokens are summed
  from the log into the trial result.

## Running it

Needs Docker (or Podman, `-e podman`) and Harbor (`uv tool install harbor`). The model
is `<Kvist provider id>/<model id>`; the key is read from the host environment under
Kvist's name for it, `<PROVIDER>_API_KEY` (`<PROVIDER>_BASE_URL` overrides the
endpoint).

```bash
export INCEPTION_API_KEY=...
cd <kvist checkout>
export PYTHONPATH=scripts/bench

# One task first, to see the whole chain work
harbor run -d terminal-bench@2.0 -i '*fix-git' -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 1

# A small sample, one attempt each, to measure tokens per task
harbor run -d terminal-bench@2.0 -l 10 -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 4

# The full leaderboard run: every task, five attempts
harbor run -d terminal-bench@2.0 -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 4 -k 5
```

`harbor jobs resume` continues a job that was cut off. Terminal-Bench 2.0 has 89 tasks;
most ask for 1 CPU and 2 GB of memory, the largest for 4 CPUs and 8 GB, each up to 10 GB
of disk. The task images stay in Docker after a run, so prune them when disk is short.

## Podman notes

Harbor's Podman environment needs a compose frontend (`podman-compose` works). Two
settings keep it from failing: `PODMAN_COMPOSE_WARNING_LOGS=false` (otherwise its
banner is read as command output) and a `registries.conf` with
`unqualified-search-registries = ["docker.io"]` (the task images use short names),
which can be passed for one run as `CONTAINERS_REGISTRIES_CONF=<file>`.
