// Stand-in for `kvist --frontend stdio-multi`: same NDJSON, scripted answers.
import readline from "node:readline"

// Stands in for the translation half of the core's bilingual messages, written
// with escapes so no Russian text lives in this file.
const TR = "\u043f\u0435\u0440\u0435\u0432\u043e\u0434"

const out = (event) => process.stdout.write(JSON.stringify(event) + "\n")
const args = process.argv.slice(2)

if (args.includes("--session-op")) {
  out({ ok: true, active_session: "chat-fake", sessions: ["chat-fake", "old"] })
  process.exit(0)
}

out({ type: "hello", protocol_version: process.env.FAKE_PROTOCOL ? Number(process.env.FAKE_PROTOCOL) : 1, provider: "fake", models: { fast: "fake-fast" }, role: "auto", session: "chat-fake" })

const waiting = new Map()
const rl = readline.createInterface({ input: process.stdin })

rl.on("line", (line) => {
  let message = null
  try {
    message = JSON.parse(line)
  } catch {}
  if (message && message.type === "cancel") {
    out({ type: "final", message: "Stopped by the user.", user_messages: [] })
    out({ type: "turn_end" })
    return
  }
  if (message && message.request_id) {
    waiting.get(message.request_id)?.(message)
    return
  }
  if (message && message.type === "command") {
    void command(message)
    return
  }
  void turn(line)
})

// One turn or one command at a time, like the real core (UI_API.md §8c).
let busy = false

// Lets a person watching a real terminal see the live region between events.
const pause = () => new Promise((resolve) => setTimeout(resolve, Number(process.env.FAKE_DELAY_MS || 0)))

async function turn(goal) {
  busy = true
  out({ type: "live_start" })
  out({ type: "status", user_message: "Reading files", status: "working", step: 1, progress: 0, plan_step: "inspect", is_error: false })
  if (goal === "ask") {
    out({ type: "input_request", request_id: "r1", kind: "confirm", title: "Run command?", body: "rm -rf build", options: null, current_value: "", help_text: "" })
    const reply = await new Promise((resolve) => waiting.set("r1", resolve))
    out({ type: "final", message: reply.approved ? "approved" : "denied", user_messages: [] })
  } else if (goal === "chat") {
    out({ type: "stream_start", stream_id: "s1" })
    out({ type: "stream_chunk", stream_id: "s1", text: "Hello **there**" })
    out({ type: "stream_commit", stream_id: "s1", text: "Hello **there**" })
  } else if (goal === "noisy") {
    // What a real run looks like: stderr chatter and notices land while an action is still running.
    out({ type: "step", step: 1, plan_step: "edit", thought: "Creating a demo file", user_message: "", action_type: "write_file", action_args: { path: "demo.txt", content: "one\ntwo\nthree\n" }, reasoning_seconds: 0 })
    process.stderr.write("warning: something noisy from the core\n")
    out({ type: "sandbox_notice", note: "sandbox: workspace mode" })
    await pause()
    out({ type: "action_result", step: 1, action_type: "write_file", ok: true, summary: "Wrote 3 lines", output: "", exit_code: null, path: "demo.txt", first_line: 1 })
    out({ type: "step", step: 2, plan_step: "edit", thought: "Replace the second line", user_message: "", action_type: "replace_in_file", action_args: { path: "demo.txt", old_text: "two", new_text: "TWO" }, reasoning_seconds: 0 })
    out({ type: "input_request", request_id: "r2", kind: "confirm", title: "Overwrite demo.txt?", body: "replace_in_file demo.txt", options: null, current_value: "", help_text: "" })
    const reply = await new Promise((resolve) => waiting.set("r2", resolve))
    process.stderr.write("more noise after approval\n")
    out({ type: "action_result", step: 2, action_type: "replace_in_file", ok: reply.approved === true, summary: reply.approved ? "Replaced 1 occurrence" : "Failed: denied", output: reply.approved ? "" : "denied", exit_code: null, path: "demo.txt", first_line: reply.approved ? 2 : null })
    out({ type: "final", message: "done: noisy", user_messages: [] })
  } else if (goal === "think") {
    out({ type: "route", role: "coder", source: "router" })
    out({ type: "stream_start", stream_id: "t1" })
    out({ type: "reasoning_start", stream_id: "t1" })
    out({ type: "reasoning_chunk", stream_id: "t1", text: "The user wants a summary.\nI should look at the entry point first." })
    await pause()
    out({ type: "reasoning_commit", stream_id: "t1", duration_seconds: 1.2 })
    out({ type: "stream_commit", stream_id: "t1", text: "Here is the summary." })
  } else if (goal === "explore") {
    out({ type: "route", role: "coder", source: "router" })
    out({ type: "step", step: 1, plan_step: "inspect", thought: "look around", user_message: "", action_type: "read_file", action_args: { path: "a.py" }, reasoning_seconds: 0 })
    await pause()
    out({ type: "step", step: 2, plan_step: "inspect", thought: "find the caller", user_message: "", action_type: "search_code", action_args: { query: "main" }, reasoning_seconds: 0 })
    await pause()
    out({ type: "step", step: 3, plan_step: "inspect", thought: "", user_message: "", action_type: "read_multiple_files", action_args: { paths: ["b.py", "c.py"] }, reasoning_seconds: 0 })
    await pause()
    out({ type: "step", step: 4, plan_step: "edit", thought: "Now the fix is clear: rename the helper.", user_message: "", action_type: "write_file", action_args: { path: "a.py" }, reasoning_seconds: 2.4 })
    await pause()
    out({ type: "step", step: 5, plan_step: "verify", thought: "check", user_message: "", action_type: "list_dir", action_args: { dir: "." }, reasoning_seconds: 0 })
    await pause()
    out({ type: "final", message: "done: explore", user_messages: [] })
  } else if (goal === "edit") {
    await editScenario()
  } else if (goal === "shell") {
    await shellScenario()
  } else if (goal === "gitdiff") {
    await gitDiffScenario()
  } else if (goal === "secret" || goal === "text") {
    // Drives the secret / text dialogs from a turn, without going through a command.
    const reply = await ask(goal === "secret" ? { kind: "secret", title: "API key for fake", help_text: "Stored in ~/.kvist/config.json" } : { kind: "text", title: "Session name", current_value: "chat-default" })
    out({ type: "final", message: `answered: ${reply.value === null ? "cancelled" : `${reply.value.length} chars`}`, user_messages: [] })
  } else if (goal === "slow") {
    await new Promise((resolve) => setTimeout(resolve, 400))
    out({ type: "final", message: "done: slow", user_messages: [] })
  } else if (goal === "approve-cmd") {
    out({ type: "route", role: "coder", source: "router" })
    out({ type: "step", step: 1, plan_step: "edit", thought: "Fetching a URL", user_message: "", action_type: "run_shell", action_args: { command: "curl -sI https://example.com | head -n 1" }, reasoning_seconds: 0, action_description: "Fetch headers" })
    await pause()
    out({
      type: "input_request",
      request_id: "a1",
      kind: "confirm",
      title: "Dangerous command",
      body: "",
      subject: { kind: "command", command: "curl -sI https://example.com | head -n 1", reason: "network access" },
      remember_pattern: "curl -sI https://example.com | head -n 1",
    })
    const a1 = await new Promise((resolve) => waiting.set("a1", resolve))
    out({ type: "action_result", step: 1, action_type: "run_shell", ok: a1.approved === true, summary: a1.approved ? "Ran command" : "Denied", output: a1.approved ? "HTTP/1.1 200 OK" : "", exit_code: a1.approved ? 0 : null, path: null, first_line: null })
    out({ type: "final", message: `done: approved=${a1.approved} remember=${a1.remember || false}`, user_messages: [] })
  } else if (goal === "approve-danger") {
    out({ type: "route", role: "coder", source: "router" })
    out({ type: "step", step: 1, plan_step: "edit", thought: "Resetting repository", user_message: "", action_type: "run_shell", action_args: { command: "git reset --hard HEAD" }, reasoning_seconds: 0 })
    await pause()
    out({
      type: "input_request",
      request_id: "a2",
      kind: "confirm",
      title: "Dangerous command",
      body: "",
      subject: { kind: "command", command: "git reset --hard HEAD", reason: "destructive git operation" },
      remember_pattern: null,
    })
    const a2 = await new Promise((resolve) => waiting.set("a2", resolve))
    out({ type: "action_result", step: 1, action_type: "run_shell", ok: a2.approved === true, summary: a2.approved ? "Reset complete" : "Denied", output: "", exit_code: a2.approved ? 0 : null, path: null, first_line: null })
    out({ type: "final", message: `done: approved=${a2.approved}`, user_messages: [] })
  } else if (goal === "approve-edit") {
    out({ type: "route", role: "coder", source: "router" })
    out({ type: "step", step: 1, plan_step: "edit", thought: "Updating config", user_message: "", action_type: "replace_in_file", action_args: { path: "config.py", old_text: "DEBUG = False", new_text: "DEBUG = True" }, reasoning_seconds: 0 })
    await pause()
    const diff = `--- a/config.py
+++ b/config.py
@@ -1,3 +1,3 @@
 import os
-DEBUG = False
+DEBUG = True
 PORT = 8080`
    out({
      type: "input_request",
      request_id: "a3",
      kind: "confirm",
      title: "File overwrite — Human-in-the-Loop",
      body: "",
      subject: { kind: "overwrite", path: "config.py", diff },
      remember_pattern: null,
    })
    const a3 = await new Promise((resolve) => waiting.set("a3", resolve))
    out({ type: "action_result", step: 1, action_type: "replace_in_file", ok: a3.approved === true, summary: a3.approved ? "Replaced 1 occurrence" : "Denied", output: "", exit_code: null, path: "config.py", first_line: a3.approved ? 2 : null })
    out({ type: "final", message: `done: approved=${a3.approved}`, user_messages: [] })
  } else {
    out({ type: "step", step: 1, plan_step: "inspect", thought: "look", user_message: "", action_type: "read_file", action_args: { path: "a.py" }, reasoning_seconds: 0 })
    out({ type: "final", message: `done: ${goal}`, user_messages: [] })
  }
  out({ type: "live_stop" })
  busy = false
  out({ type: "turn_end" })
}

const step = (n, action_type, action_args, extra = {}) =>
  out({ type: "step", step: n, plan_step: "edit", thought: "", user_message: "", action_type, action_args, reasoning_seconds: 0, ...extra })
const result = (n, action_type, fields) =>
  out({ type: "action_result", step: n, action_type, ok: true, summary: "", output: "", exit_code: null, path: null, first_line: null, ...fields })

const OLD_BLOCK = ["def total(items):", "    result = 0", "    for item in items:", "        result += item.price", "    return result", "", "print(total([]))"].join("\n")
const NEW_BLOCK = ["def total(items):", "    result = 0", "    for item in items:", "        if item.price:", "            result += item.price", "    return result", "", "print(total([]))"].join("\n")

async function editScenario() {
  out({ type: "route", role: "coder", source: "router" })
  step(1, "write_file", { path: "app.py", content: Array.from({ length: 16 }, (_, i) => `line ${i + 1}`).join("\n") })
  await pause()
  result(1, "write_file", { summary: "Wrote 16 lines", path: "app.py", first_line: 1 })
  step(2, "replace_in_file", { path: "app.py", old_text: OLD_BLOCK, new_text: NEW_BLOCK, count: 1 })
  await pause()
  result(2, "replace_in_file", { summary: "Replaced 1 occurrence", path: "app.py", first_line: 17 })
  step(3, "append_file", { path: "app.py", content: "print('done')\nprint('bye')" })
  await pause()
  result(3, "append_file", { summary: "Appended 2 lines", path: "app.py", first_line: 25 })
  step(4, "replace_in_file", { path: "app.py", old_text: "missing", new_text: "x", count: 1 })
  await pause()
  result(4, "replace_in_file", { ok: false, summary: "Failed: old_text fragment not found in file", output: "old_text fragment not found in file", path: "app.py" })
  step(5, "read_file", { path: "nope.py" })
  result(5, "read_file", { ok: false, summary: "Failed: file not found", output: "file not found" })
  step(6, "web_search", { query: "ink static" })
  await pause()
  result(6, "web_search", { summary: "5 results" })
  step(7, "write_file", { path: "blocked.py", content: "x" })
  step(8, "list_dir", { dir: "." })
  result(8, "list_dir", { summary: "3 entries" })
  out({ type: "final", message: "done: edit", user_messages: [] })
}

async function shellScenario() {
  step(1, "run_shell", { command: ["npm", "test"] })
  await pause()
  result(1, "run_shell", { summary: "Command exited with code 0", output: "> vitest\n\u001b[32m✓\u001b[0m 3 passed", exit_code: 0 })
  step(2, "run_shell", { command: ["pytest", "-q"] })
  await pause()
  result(2, "run_shell", { ok: false, summary: "Command exited with code 2", output: "collected 3\n1 failed", exit_code: 2 })
  step(3, "run_shell", { command: "true" })
  result(3, "run_shell", { summary: "Command exited with code 0", exit_code: 0 })
  step(4, "run_shell", { command: ["make", "all"] })
  await pause()
  result(4, "run_shell", { summary: "Command exited with code 0", output: Array.from({ length: 40 }, (_, i) => `building module ${i + 1}`).join("\n"), exit_code: 0 })
  step(5, "run_shell", { command: ["sleep", "999"] })
  result(5, "run_shell", { ok: false, summary: "Command timed out after 30s", output: "" })
  out({ type: "final", message: "done: shell", user_messages: [] })
}

async function gitDiffScenario() {
  step(1, "git_diff", {})
  await pause()
  const diff = ["diff --git a/x.py b/x.py", "index 1..2 100644", "--- a/x.py", "+++ b/x.py", "@@ -1,3 +1,3 @@", " keep", "-old line", "+new line", " tail"].join("\n")
  result(1, "git_diff", { summary: "1 file in diff", output: diff })
  out({ type: "final", message: "done: gitdiff", user_messages: [] })
}

// ---- slash commands (UI_API.md §8c) ----

let requestCounter = 0
async function ask(fields) {
  const request_id = `c${++requestCounter}`
  out({ type: "input_request", request_id, kind: "choice", title: "", body: "", options: null, current_value: "", help_text: "", ...fields })
  return new Promise((resolve) => waiting.set(request_id, resolve))
}

const finish = (name, ok, message, effects = {}) => out({ type: "command_result", name, ok, message, effects })
const cancelled = (name) => finish(name, true, `Cancelled / ${TR}`, { cancelled: true })
const option = (value, label, description) => ({ value, label, ...(description ? { description } : {}) })

const ROLE_OPTIONS = [
  option("auto", "Auto (automatic role detection)", "Pick the role per request"),
  option("coder", "Coder", "Edits code and runs checks"),
  option("reader", "Reader", "Reads and reports, never writes"),
  option("devops", "DevOps", "Shell and infrastructure work"),
  option("chat", "Chat", "Plain conversation, no tools"),
]
const EFFORT_OPTIONS = ["false", "low", "medium", "high", "xhigh", "max"].map((v) => option(v, v))
// Long enough to need scrolling and a search; descriptions carry a searchable word.
const BIG_LIST = Array.from({ length: 45 }, (_, i) => {
  const n = String(i + 1).padStart(2, "0")
  return option(`model-${n}`, `fake-model-${n}`, i % 5 === 0 ? `flagship tier ${n}` : `standard tier ${n}`)
})
const SMALL_LIST = ["alpha", "beta", "gamma"].map((v) => option(v, v, `the ${v} model`))

const handlers = {
  async role(args) {
    const value = args[0] ?? (await ask({ title: "Select Agent Role", options: ROLE_OPTIONS, current_value: "auto", help_text: "Applies to every following request" })).value
    if (value === null || value === undefined) return cancelled("role")
    finish("role", true, `Role set to: ${value} / ${TR}`, { role: value })
  },
  async effort(args) {
    const value = args[0] ?? (await ask({ title: "Reasoning effort", options: EFFORT_OPTIONS, current_value: "medium" })).value
    if (value === null || value === undefined) return cancelled("effort")
    finish("effort", true, `Reasoning effort set to: ${value} / ${TR}`, { effort: value, advisory: "Maps to the provider's high tier." })
  },
  async verbose(args) {
    const enabled = args[0] !== "false" && args[0] !== "off"
    finish("verbose", true, enabled ? `Verbose mode enabled. / ${TR}` : `Verbose mode disabled. / ${TR}`, { verbose: enabled })
  },
  async models() {
    const fast = (await ask({ title: "Fast model", body: "Used for routine steps.", options: BIG_LIST, current_value: "model-03" })).value
    if (fast == null) return cancelled("models")
    const smart = (await ask({ title: "Smart model", options: SMALL_LIST, current_value: "beta" })).value
    if (smart == null) return cancelled("models")
    finish("models", true, `Models updated. / ${TR}`, { provider: "fake", models: { fast, smart }, warnings: [`Model beta is slow. / ${TR}`] })
  },
  async session(args) {
    if (args.length) return finish("session", true, `Switched to session ${args[1] ?? args[0]}. / ${TR}`, { session: args[1] ?? args[0], sessions: ["chat-fake", "old"], has_checkpoint: false, changed: true })
    const value = (await ask({ title: "Sessions", options: [option("chat-fake", "chat-fake", "active"), option("old", "old", "3 turns")], current_value: "chat-fake" })).value
    if (value == null) return cancelled("session")
    finish("session", true, `Active session: ${value} / ${TR}`, { session: value, sessions: ["chat-fake", "old"], has_checkpoint: false, changed: value !== "chat-fake" })
  },
  async new(args) {
    const name = args[0] ?? "chat-new"
    finish("new", true, `Session ${name} created. / ${TR}`, { session: name, sessions: ["chat-fake", name], has_checkpoint: false, changed: true })
  },
  async changes() {
    await pause()
    const lines = Array.from({ length: 60 }, (_, i) => `M file${i + 1}.py`)
    finish("changes", true, ["Changes in 60 files:", ...lines].join("\n"))
  },
  // Not real core commands: they exist to drive the secret / text / confirm dialogs.
  async secret() {
    const value = (await ask({ kind: "secret", title: "API key for fake", help_text: "Stored in ~/.kvist/config.json" })).value
    if (value == null) return cancelled("secret")
    finish("secret", true, `Key saved (${value.length} characters). / ${TR}`, {})
  },
  async text() {
    const value = (await ask({ kind: "text", title: "Session name", current_value: "chat-default" })).value
    if (value == null) return cancelled("text")
    finish("text", true, `Named: ${value} / ${TR}`, {})
  },
  async confirm() {
    const reply = await ask({ kind: "confirm", title: "Delete provider?", body: "This removes the saved key." })
    finish("confirm", true, reply.approved ? `Deleted. / ${TR}` : `Kept. / ${TR}`, {})
  },
  async provider() {
    const picked = (await ask({
      title: "Select LLM Provider",
      options: [option("openai", "OpenAI", "GPT models"), option("anthropic", "Anthropic", "Claude models"), option("deepseek", "DeepSeek", "DeepSeek models")],
      current_value: "openai",
    })).value
    if (picked == null) return cancelled("provider")
    const key = (await ask({ kind: "secret", title: `API key for ${picked}`, help_text: "Leave empty to keep the saved one" })).value
    if (key == null) return cancelled("provider")
    finish("provider", true, `Provider: ${picked} / ${TR}`, { provider: picked, models: { fast: "new-fast" } })
  },
  async "change-api"() {
    const key = (await ask({ kind: "secret", title: "New API key for fake" })).value
    if (key == null) return cancelled("change-api")
    finish("change-api", true, `Key updated (${key.length} characters). / ${TR}`, {})
  },
  async "settings-api"() {
    const row = (await ask({
      title: "API settings",
      options: [option("request_interval", "Request interval", "seconds between calls"), option("request_timeout", "Request timeout", "seconds"), option("done", "Done", "")],
      current_value: "request_interval",
    })).value
    if (row == null || row === "done") return cancelled("settings-api")
    const value = (await ask({ kind: "text", title: `Value for ${row}`, current_value: "2" })).value
    if (value == null) return cancelled("settings-api")
    finish("settings-api", true, `${row} = ${value} / ${row} = ${value}`, { changed_keys: [row] })
  },
  async strime(args) {
    const enabled = args[0] ? args[0] !== "false" && args[0] !== "off" : true
    finish("strime", true, enabled ? `Streaming enabled. / ${TR}` : `Streaming disabled. / ${TR}`, { stream: enabled })
  },
  async "del-provider"() {
    const reply = await ask({ kind: "confirm", title: "Delete custom provider 'fake'?", body: "This cannot be undone." })
    if (!reply.approved) return finish("del-provider", true, `Kept. / ${TR}`, { cancelled: true })
    finish("del-provider", true, `Provider deleted. / ${TR}`, { provider: "", restart_required: true })
  },
  async "full-reset"() {
    const first = await ask({ kind: "confirm", title: "Erase every saved provider and key?", body: "Everything in ~/.kvist/config.json is removed." })
    if (!first.approved) return finish("full-reset", true, `Kept. / ${TR}`, { cancelled: true })
    const second = await ask({ kind: "confirm", title: "Final confirmation", body: "This cannot be undone." })
    if (!second.approved) return finish("full-reset", true, `Kept. / ${TR}`, { cancelled: true })
    finish("full-reset", true, `Settings erased. / ${TR}`, { restart_required: true })
  },
  async revert() {
    const reply = await ask({
      kind: "confirm",
      title: "Revert 2 file(s)?",
      body: "src/app.py — restore to baseline\nsrc/util.py — restore to baseline",
    })
    if (!reply.approved) return finish("revert", true, `Nothing reverted. / ${TR}`, { cancelled: true })
    finish("revert", true, `Reverted 2 files. / ${TR}`, { reverted: [{ path: "src/app.py" }, { path: "src/util.py" }] })
  },
}

async function command(message) {
  const name = String(message.name || "").toLowerCase()
  if (busy) {
    finish(name, false, `A turn or another command is running / ${TR}`)
    return
  }
  // `resume` is a turn, not a wizard: the real core queues it past the command
  // handlers and answers with the run's events and `turn_end`.
  if (name === "resume") {
    busy = true
    out({ type: "session", session: "chat-interrupted", has_checkpoint: true })
    out({ type: "live_start" })
    out({ type: "status", user_message: "Continuing the interrupted run", status: "working", step: 4, progress: 0, plan_step: "edit", is_error: false })
    await pause()
    out({ type: "final", message: "Resumed and finished.", user_messages: [] })
    out({ type: "live_stop" })
    busy = false
    out({ type: "turn_end" })
    return
  }
  const handler = handlers[name]
  if (!handler) {
    finish(name, false, `Unknown command: ${name} / ${TR}`)
    return
  }
  busy = true
  try {
    await handler(Array.isArray(message.args) ? message.args : [])
  } finally {
    busy = false
  }
}

rl.on("close", () => process.exit(0))
