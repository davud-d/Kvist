// A scripted stand-in for the Kvist core, used when the page is opened outside
// Tauri. It speaks the same events (docs/UI_API.md §7) on timers, so every
// part of the interface can be seen and tried without a model. Goals with a
// keyword steer it: "approve", "plan", "insult"; anything else is a normal turn.

const MANIFEST = {
  type: "manifest",
  roles: ["auto", "coder", "reader", "devops", "chat", "universal"].map((value) => ({ value, description: `${value} role` })),
  effort_levels: ["false", "low", "medium", "high", "xhigh", "max"].map((value) => ({ value, description: `${value} effort`, default: value === "medium" ? "true" : "" })),
  styles: ["Default", "Senior-dev", "Friendly Developer", "Minimalist"].map((value) => ({ value, description: `${value} style` })),
  slash_commands: [
    { name: "new", description: "Start a new session" },
    { name: "role", description: "Choose the agent role" },
    { name: "effort", description: "Set the reasoning effort" },
    { name: "models", description: "Choose the models" },
    { name: "session", description: "Switch session" },
    { name: "changes", description: "Show what the agent changed" },
    { name: "best-of", description: "Several agents try one task", via: "core" },
    { name: "council", description: "Four angles on one question", via: "core" },
  ],
  api_settings: [],
};

const BROWSER_PAGE = (title) =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="#f4efe6"/><rect width="640" height="34" fill="#2b2f45"/><rect x="14" y="10" width="14" height="14" fill="#ff7a59"/><rect x="36" y="10" width="14" height="14" fill="#ffc857"/><rect x="58" y="10" width="14" height="14" fill="#6fd08c"/><rect x="92" y="8" width="420" height="18" fill="#454a6b"/><text x="40" y="96" font-family="monospace" font-size="30" font-weight="bold" fill="#2b2f45">${title}</text><rect x="40" y="120" width="360" height="14" fill="#cfc8b8"/><rect x="40" y="146" width="300" height="14" fill="#cfc8b8"/><rect x="40" y="172" width="330" height="14" fill="#cfc8b8"/><rect x="40" y="230" width="150" height="46" fill="#e8743b"/></svg>`,
  );

export function createMock(emit) {
  const timers = new Map();
  const pending = new Map();
  const sessions = new Map();

  const after = (agent, ms, event) => {
    const timer = setTimeout(() => emit(agent, event), ms);
    timers.set(agent, [...(timers.get(agent) ?? []), timer]);
  };
  const script = (agent, steps) => {
    let at = 0;
    for (const [gap, event] of steps) {
      at += gap;
      after(agent, at, event);
    }
    return at;
  };
  const cancel = (agent) => {
    (timers.get(agent) ?? []).forEach(clearTimeout);
    timers.delete(agent);
  };

  function normalTurn(agent, text) {
    const answer = `Done. I looked at the project and changed what **${text.replace(/[$\\]/g, "").slice(0, 40)}** needed:\n\n- fixed the parser edge case\n- added a test\n\nThe cost per line is $O(n)$, and for all rows\n\n$$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$$\n\n\`\`\`python\ndef parse(line):\n    return line.strip().split(",")\n\`\`\`\n`;
    let total = script(agent, [
      [60, { type: "route", role: "coder", source: "llm" }],
      [0, { type: "live_start" }],
      [80, { type: "status", user_message: "Thinking", status: "working", step: 1, progress: 10, plan_step: "inspect", is_error: false }],
      [60, { type: "reasoning_start", stream_id: "t1" }],
      [260, { type: "reasoning_chunk", stream_id: "t1", text: "The request touches the parser. I should read it first, then run the tests." }],
      [500, { type: "reasoning_commit", stream_id: "t1", duration_seconds: 1.8 }],
      [40, { type: "step", step: 1, plan_step: "inspect", user_message: "Reading the parser first.", action_type: "read_file", action_args: { path: "src/parser.py" }, action_description: "reads src/parser.py", call_id: "c1", batch_id: "b1" }],
      [0, { type: "step", step: 2, plan_step: "inspect", user_message: "", action_type: "search_code", action_args: { query: "parse(" }, action_description: 'searches "parse("', call_id: "c2", batch_id: "b1" }],
      [300, { type: "action_result", step: 1, action_type: "read_file", ok: true, summary: "84 lines", output: "", call_id: "c1", batch_id: "b1" }],
      [80, { type: "action_result", step: 2, action_type: "search_code", ok: true, summary: "6 matches", output: "", call_id: "c2", batch_id: "b1" }],
      [200, { type: "helper", agent_id: "h1", event: "started", state: "running", session: "helper-1", task: "Run the test suite and report failures", summary: "" }],
      ...[0, 16, 32, 48].map((from) => [140, { type: "call_stream", call_id: "c2w", tool: "write_file", path: "src/util.py", field: "content", line: from, text: Array.from({ length: 8 }, (_, i) => `def helper_${from / 2 + i}(value):\n    return value + ${from / 2 + i}\n`).join(""), done: false }]),
      [60, { type: "step", step: 3, plan_step: "edit", user_message: "", action_type: "write_file", action_args: { path: "src/util.py" }, action_description: "writes src/util.py", call_id: "c2w", batch_id: "b2" }],
      [200, { type: "action_result", step: 3, action_type: "write_file", ok: true, summary: "Wrote 64 lines", output: "", path: "src/util.py", lines_added: 64, lines_removed: 0, call_id: "c2w", batch_id: "b2" }],
      [90, { type: "call_stream", call_id: "c3", tool: "replace_in_file", path: "src/parser.py", field: "old_text", line: 0, text: "def parse(line):\n    return line.split(',')\n", done: false }],
      [90, { type: "call_stream", call_id: "c3", tool: "replace_in_file", path: "src/parser.py", field: "new_text", line: 0, text: "def parse(line):\n    cells = line.strip().split(',')\n", done: false }],
      [90, { type: "call_stream", call_id: "c3", tool: "replace_in_file", path: "src/parser.py", field: "new_text", line: 2, text: "    return [c.strip() for c in cells]\n", done: false }],
      [60, { type: "step", step: 3, plan_step: "edit", user_message: "Fixing it.", action_type: "replace_in_file", action_args: { path: "src/parser.py" }, action_description: "edits src/parser.py", call_id: "c3", batch_id: "b2" }],
      [60, { type: "helper_activity", agent_id: "h1", phase: "step", call_id: "hc1", action_type: "run_shell", label: "runs pytest -q" }],
      [400, { type: "action_result", step: 3, action_type: "replace_in_file", ok: true, summary: "Edited 1 place", output: "", path: "src/parser.py", first_line: 41, lines_added: 3, lines_removed: 1, patch: "--- a/src/parser.py\n+++ b/src/parser.py\n@@ -40,3 +40,5 @@\n def parse(line):\n-    return line.split(',')\n+    cells = line.strip().split(',')\n+    return [c.strip() for c in cells]\n", call_id: "c3", batch_id: "b2" }],
      [100, { type: "screen", screen_id: "s1", skill: "browser", title: "docs.example.org", state: "running", url: "http://127.0.0.1:9/view", stream_url: BROWSER_PAGE("Parser docs"), frame_url: BROWSER_PAGE("Parser docs") }],
      [140, { type: "step", step: 4, plan_step: "verify", user_message: "Running the tests.", action_type: "run_shell", action_args: { command: ["pytest", "-q"] }, action_description: "runs the tests", call_id: "c4", batch_id: "b3" }],
      [250, { type: "action_output", step: 4, call_id: "c4", output: "collected 12 items\n......." }],
      [450, { type: "action_output", step: 4, call_id: "c4", output: "collected 12 items\n............" }],
      [200, { type: "action_result", step: 4, action_type: "run_shell", ok: true, summary: "Command exited with code 0", output: "collected 12 items\n............\n12 passed in 0.42s\n", exit_code: 0, call_id: "c4", batch_id: "b3" }],
      [60, { type: "helper_activity", agent_id: "h1", phase: "result", call_id: "hc1", action_type: "run_shell", label: "runs pytest -q", ok: true, summary: "12 passed", output: "12 passed in 0.42s" }],
      [100, { type: "helper", agent_id: "h1", event: "done", state: "done", session: "helper-1", task: "Run the test suite and report failures", summary: "All 12 tests pass" }],
      [100, { type: "context", context_tokens: 18400, context_limit: 200000, prompt: 17000, generated: 1400, pending: 0 }],
      [80, { type: "stream_start", stream_id: "a1" }],
    ]);
    for (let i = 12; i < answer.length + 12; i += 12) {
      total += 35;
      after(agent, total, { type: "stream_chunk", stream_id: "a1", text: answer.slice(0, i) });
    }
    script(agent, [[total + 60, { type: "stream_commit", stream_id: "a1", text: answer }], [10, { type: "step", step: 5, plan_step: "done", user_message: "", action_type: "final_answer", action_args: {}, action_description: "отвечает", call_id: "c5", batch_id: "b4" }], [40, { type: "final", message: answer, user_messages: [], outcome: "done" }], [30, { type: "turn_end" }]]);
  }

  function ask(agent, request, then) {
    const id = `req-${Math.random().toString(36).slice(2, 8)}`;
    pending.set(id, then);
    after(agent, 120, { type: "input_request", request_id: id, body: "", options: null, current_value: "", help_text: "", ...request });
  }

  function goal(agent, envelope) {
    const text = envelope.startsWith('{"type":"kvist-image-goal"') ? JSON.parse(envelope).text : envelope;
    const lower = text.toLowerCase();
    if (lower.includes("insult")) {
      script(agent, [
        [60, { type: "route", role: "chat", source: "llm" }],
        [200, { type: "session_ended", session_id: "mock", warned_msg_id: 1, closed_msg_id: 2, reason: "abuse", closed_notice: "Conversation closed. Press Enter for a new session." }],
        [40, { type: "final", message: "I am ending this conversation here.", user_messages: [], outcome: "incomplete", closed: true }],
        [20, { type: "turn_end" }],
      ]);
      return;
    }
    if (lower.includes("approve")) {
      script(agent, [[60, { type: "route", role: "devops", source: "llm" }], [0, { type: "live_start" }]]);
      ask(agent, { kind: "confirm", title: "Run this command?", subject: { kind: "command", command: ["npm", "install", "left-pad"], reason: "installs a dependency" }, remember_pattern: "npm install *" }, (answer) => {
        script(agent, [[80, { type: "final", message: answer.approved ? "Installed." : "Skipped the install.", user_messages: [], outcome: "done" }], [20, { type: "turn_end" }]]);
      });
      return;
    }
    if (lower.includes("plan")) {
      ask(agent, { kind: "choice", title: "This touches many files. A plan first is safer; starting now is faster but riskier.", options: [{ value: "plan", label: "Plan first" }, { value: "go", label: "Start now" }] }, () => {
        script(agent, [[60, { type: "plan", markdown: "1. Read the parser\n2. Add the failing test\n3. Fix the edge case" }], [40, { type: "final", message: "Plan ready.", user_messages: [], outcome: "done" }], [20, { type: "turn_end" }]]);
      });
      return;
    }
    const replay = typeof location === "object" ? new URLSearchParams(location.search).get("replay") : null;
    if (replay) {
      replayTurn(agent, replay);
      return;
    }
    normalTurn(agent, text);
  }

  // `?replay=<file>`: plays events a real core wrote (scripts/demo_stream.py record) at the times they came.
  async function replayTurn(agent, url) {
    const lines = (await (await fetch(url)).text()).split("\n").filter(Boolean).map((line) => JSON.parse(line));
    const turn = lines.filter((event) => !["hello", "session_title", "session"].includes(event.type) && event.at_ms > 0);
    let before = turn[0]?.at_ms ?? 0;
    script(agent, turn.map(({ at_ms, ...event }) => {
      const gap = at_ms - before;
      before = at_ms;
      return [gap, event];
    }));
  }

  // `/best-of N <task>`: a whole turn that ends in the person's decision (docs/UI_API.md §7), then `command_result`.
  function bestOf(agent, args) {
    const run = "run-1";
    const exec = (id, style) => ({ executor_id: id, style, branch: `kvist/bestof/${run}/${id}`, session: `bo-${id}` });
    const snap = (id, over) => ({ type: "bestof_status", run_id: run, executor_id: id, style: "", branch: "", state: "running", step: 1, tests: { state: "unknown", summary: "" }, diff: { files: 0, added: 0, removed: 0 }, usage: { input_tokens: 4000, output_tokens: 300, cost_approx: false, cost_usd: 0.012 }, ...over });
    const tests = (state, summary) => ({ state, summary });
    const rows = [
      { executor_id: "e1", style: "senior-dev", outcome: "finalist", tests: tests("passed", "12 passed"), diff: { files: 3, added: 41, removed: 6 }, note: "works, but touches more files" },
      { executor_id: "e2", style: "minimalist", outcome: "winner", tests: tests("passed", "12 passed"), diff: { files: 1, added: 9, removed: 2 }, note: "smallest change" },
      { executor_id: "e3", style: "friendly-dev", outcome: "eliminated", tests: tests("failed", "3 failed"), diff: { files: 2, added: 17, removed: 1 }, note: "tests keep failing" },
    ];
    const decide = (id) => {
      pending.set(id, (answer) => {
        const value = answer.value;
        const finish = (outcome, message, extra = {}) => script(agent, [[60, { type: "bestof_finished", run_id: run, outcome, message, ...extra }], [30, { type: "turn_end" }], [30, { type: "command_result", name: "best-of", ok: true, message: "Done / Готово", effects: {} }]]);
        if (typeof value === "string" && value.startsWith("diff:")) {
          const next = `req-${Math.random().toString(36).slice(2, 8)}`;
          decide(next);
          script(agent, [
            [60, { type: "bestof_diff", run_id: run, executor_id: value.slice(5), patch: "diff --git a/src/parser.py b/src/parser.py\n--- a/src/parser.py\n+++ b/src/parser.py\n@@ -40,3 +40,5 @@\n def parse(line):\n-    return line.split(',')\n+    cells = line.strip().split(',')\n+    return [c.strip() for c in cells]\n", files: ["src/parser.py"] }],
            [30, decisionRequest(next)],
          ]);
        } else if (value === "accept") finish("accepted", "Took the result of minimalist", { executor_id: "e2", commit: "9f3c2ab" });
        else if (value === "discard") finish("discarded", "Nothing was taken");
        else if (value === null) finish("cancelled", "No decision for now");
        else finish("accepted", `Took ${value}`, { executor_id: String(value).replace("pick:", "") });
      });
    };
    const decisionRequest = (id) => ({ type: "input_request", request_id: id, kind: "choice", title: "Which result do we take?", body: "", options: ["accept", "diff", "other", "discard"].map((value) => ({ value, label: value })), current_value: "", help_text: "", purpose: "bestof_decision", run_id: run });
    const first = `req-${Math.random().toString(36).slice(2, 8)}`;
    decide(first);
    script(agent, [
      [40, { type: "live_start" }],
      [60, { type: "tool_mode", mode: "native", reason: "probe_ok" }],
      [40, { type: "bestof_started", run_id: run, task: args.slice(1).join(" ") || "fix the parser", count: 3, base: "1a2b3c4", executors: [exec("e1", "senior-dev"), exec("e2", "minimalist"), exec("e3", "friendly-dev")] }],
      [500, snap("e1", { step: 2 })],
      [0, { type: "executor_activity", run_id: run, executor_id: "e1", phase: "step", call_id: "x1", action_type: "read_file", label: "reads src/parser.py", summary: "", output: "" }],
      [400, { type: "executor_activity", run_id: run, executor_id: "e1", phase: "result", call_id: "x1", action_type: "read_file", label: "reads src/parser.py", ok: true, summary: "84 lines", output: "" }],
      [300, snap("e2", { step: 3, diff: { files: 1, added: 9, removed: 2 } })],
      [0, { type: "executor_activity", run_id: run, executor_id: "e2", phase: "step", call_id: "y1", action_type: "run_shell", label: "runs pytest -q", summary: "", output: "" }],
      [600, snap("e3", { state: "failed", step: 6, tests: tests("failed", "3 failed"), diff: { files: 2, added: 17, removed: 1 } })],
      [0, { type: "bestof_eliminated", run_id: run, executor_id: "e3", reason: "tests keep failing" }],
      [500, snap("e1", { state: "finished", step: 9, tests: tests("passed", "12 passed"), diff: { files: 3, added: 41, removed: 6 } })],
      [200, snap("e2", { state: "finished", step: 7, tests: tests("passed", "12 passed"), diff: { files: 1, added: 9, removed: 2 } })],
      [200, { type: "bestof_verdict", run_id: run, rows, winner: "e2", reason: "e2 passes all the tests with the smallest change" }],
      [30, { type: "bestof_finished", run_id: run, outcome: "pending", message: "Waiting for your decision" }],
      [30, decisionRequest(first)],
    ]);
  }

  // `/council <question>`: four members, their cards, objections and a verdict; then `turn_end` and `command_result`.
  function council(agent, args) {
    const id = "council-1";
    const angles = ["simplicity", "security", "speed", "maintenance"];
    const card = (angle, decision, argument) => ({ type: "council_card", council_id: id, executor_id: angle, angle, decision, argument });
    script(agent, [
      [40, { type: "live_start" }],
      [60, { type: "council_started", council_id: id, question: args.join(" ") || "Which queue should the worker use?", members: angles.map((angle) => ({ executor_id: angle, angle, session: `co-${angle}` })) }],
      [600, card("simplicity", "A plain list", "It is enough for the load we have.")],
      [500, card("security", "A bounded queue", "An unbounded list grows without limit under a flood.")],
      [400, card("speed", "A plain list, bounded later", "Ship the list now and add a limit when it hurts.")],
      [500, card("maintenance", "A bounded queue from the standard library", "No code of our own to keep alive.")],
      [300, { type: "council_objection", council_id: id, executor_id: "security", angle: "security", risks: [{ target: "simplicity", risk: "grows without limit" }, { target: "speed", risk: "the limit is added after the first outage" }] }],
      [300, { type: "council_objection", council_id: id, executor_id: "simplicity", angle: "simplicity", risks: [{ target: "maintenance", risk: "a new dependency for a small need" }] }],
      [500, { type: "council_verdict", council_id: id, chosen: "maintenance", decision: "A bounded queue from the standard library", reason: "It is safe, small and nothing of our own to maintain.", dissent: [{ executor_id: "speed", angle: "speed", why: "a limit now costs time we do not have" }] }],
      [40, { type: "turn_end" }],
      [30, { type: "command_result", name: "council", ok: true, message: "Done / Готово", effects: {} }],
    ]);
  }

  function command(agent, message) {
    const name = message.name;
    const result = (ok, text, effects = {}) => after(agent, 40, { type: "command_result", name, ok, message: text, effects });
    if (name === "best-of") bestOf(agent, message.args ?? []);
    else if (name === "council") council(agent, message.args ?? []);
    else if (name === "role") result(true, `Role set to: ${message.args?.[0]} / Роль: ${message.args?.[0]}`, { role: message.args?.[0] });
    else if (name === "effort") result(true, `Effort: ${message.args?.[0]} / Усилие: ${message.args?.[0]}`, { effort: message.args?.[0], advisory: "" });
    else if (name === "style") result(true, "Style set / Стиль выбран", { style: { name: message.args?.[0], applied_to_session: true } });
    else if (name === "verbose") result(true, "Verbose toggled / Лог переключён", { verbose: true });
    else if (name === "new") {
      after(agent, 20, { type: "session", session: `chat-${Date.now()}`, has_checkpoint: false });
      result(true, "New session / Новая сессия", { session: `chat-${Date.now()}`, sessions: [], has_checkpoint: false, changed: true });
    } else if (name === "catalog")
      result(true, "", { providers: [{ provider: "deepseek", label: "DeepSeek", current: true, chosen: { fast: "ds/flash", smart: "ds/pro" }, models: ["ds/flash", "ds/pro", "cc/sonnet"] }, { provider: "openai", label: "OpenAI", current: false, chosen: { fast: "gpt-5-mini", smart: "gpt-5" }, models: ["gpt-5-mini", "gpt-5"] }] });
    else if (name === "models") result(true, "Models saved / Модели сохранены", { provider: message.args?.[0], models: { fast: message.args?.[1], smart: message.args?.[2] } });
    else if (name === "rename") result(true, "Renamed / Переименовано", { session: "mock", title: (message.args ?? []).join(" ") });
    else if (name === "changes") result(true, "src/parser.py  +3 −1 / src/parser.py  +3 −1", {});
    else result(true, `${name} ok / ${name} выполнено`, {});
  }

  return {
    async invoke(name, args) {
      if (name === "launch_dir") return "/home/demo/kvist-project";
      if (name === "window_mode") return { transparent: true, safe: false };
      if (name === "set_transparent" || name === "restart_app") return undefined;
      if (name === "agent_open") {
        sessions.set(args.id, args.session);
        after(args.id, 150, { type: "hello", protocol_version: 1, provider: "deepseek", models: { fast: "ds/flash", smart: "ds/pro" }, role: "auto", approval_mode: "ask", session: args.session ?? "chat-demo", has_checkpoint: false, effort: "medium", commands: ["new", "role", "effort", "style", "models", "session", "catalog", "rename", "changes", "verbose", "resume"] });
        after(args.id, 170, MANIFEST);
        return undefined;
      }
      if (name === "agent_close") {
        cancel(args.id);
        return undefined;
      }
      if (name === "session_op") {
        if (args.op === "list" || args.op === "status") return { ok: true, active_session: "chat-demo", sessions: ["chat-demo", "older-work"], session_items: [{ name: "chat-demo", title: "Fix the parser", updated_at: "", closed: false }, { name: "older-work", title: "Docs cleanup", updated_at: "", closed: false }] };
        if (args.op === "history" && /^bo-/.test(args.sessionName ?? "")) {
          const who = args.sessionName.slice(3);
          return { ok: true, has_more: false, events: [
            { event: { type: "user", text: "fix the parser", executor_id: who } },
            { event: { type: "step", step: 1, call_id: "j1", action_type: "read_file", action_args: { path: "src/parser.py" }, action_description: "reads src/parser.py", user_message: "Reading the parser first.", executor_id: who } },
            { event: { type: "action_result", step: 1, call_id: "j1", ok: true, summary: "84 lines", output: "", executor_id: who } },
            { event: { type: "step", step: 2, call_id: "j2", action_type: "run_shell", action_args: { command: ["pytest", "-q"] }, action_description: "runs the tests", user_message: "", executor_id: who } },
            { event: { type: "action_result", step: 2, call_id: "j2", ok: true, summary: "Command exited with code 0", output: "12 passed in 0.4s\n", exit_code: 0, executor_id: who } },
          ] };
        }
        if (args.op === "history") return { ok: true, has_more: false, events: [{ event: { type: "user", text: "Earlier request" } }, { event: { type: "stream_commit", stream_id: "h", text: "Earlier answer." } }, { event: { type: "turn_end" } }] };
        if (args.op === "input-history") return { ok: true, entries: ["fix the parser", "run the tests"] };
        return { ok: true };
      }
      if (name === "agent_send") {
        const { id, message } = args;
        if (message.request_id) pending.get(message.request_id)?.(message), pending.delete(message.request_id);
        else if (message.type === "goal") goal(id, message.text);
        else if (message.type === "command") command(id, message);
        else if (message.type === "cancel" || message.type === "pause") {
          cancel(id);
          script(id, [[20, { type: "final", message: "Stopped.", user_messages: [], outcome: "incomplete", paused: message.type === "pause" }], [10, { type: "turn_end" }]]);
        }
      }
      return undefined;
    },
    async pickFolder() {
      return "/home/demo/another-project";
    },
  };
}
