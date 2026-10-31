// Run: node --test frontends/gui/tauri/tests
import assert from "node:assert/strict";
import test from "node:test";

import { renderMarkdown, safeUrl } from "../ui/js/markdown.js";
import { springEasing, springSamples, springs, boxTransform } from "../ui/js/motion.js";
import { createAgent, reduce, replayHistory, summary, beginTurn } from "../ui/js/reducer.js";
import { buildTree } from "../ui/js/projects.js";

const feed = (agent, ...events) => events.forEach((ev) => reduce(agent, ev));
const kinds = (agent) => agent.items.map((item) => item.kind);

test("markdown escapes markup and keeps only our tags", () => {
  assert.equal(renderMarkdown("<script>alert(1)</script>"), "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>");
  assert.match(renderMarkdown("**bold** and `a<b`"), /<strong>bold<\/strong> and <code class="md-code">a&lt;b<\/code>/);
});

test("markdown fences, lists and links", () => {
  const html = renderMarkdown("# Title\n\n- one\n- two\n\n```py\nx = '<'\n```\n[docs](https://example.org/a?b=1&c=2)");
  assert.match(html, /<h3 class="md-h">Title<\/h3>/);
  assert.match(html, /<ul class="md-list"><li>one<\/li><li>two<\/li><\/ul>/);
  assert.match(html, /<pre class="md-pre" data-lang="py"><code>x = &#39;&lt;&#39;<\/code><\/pre>/);
  assert.match(html, /<a href="https:\/\/example.org\/a\?b=1&amp;c=2" data-external>docs<\/a>/);
});

test("markdown never makes a link out of javascript:", () => {
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.ok(!renderMarkdown("[x](javascript:alert(1))").includes("<a "));
});

test("a spring settles on one and takes finite time", () => {
  const { samples, seconds } = springSamples(springs.soft);
  assert.equal(samples[0], 0);
  assert.equal(samples.at(-1), 1);
  assert.ok(seconds > 0.1 && seconds < 2.3);
  assert.ok(Math.max(...springSamples(springs.bouncy).samples) > 1, "a bouncy spring overshoots");
  const { easing, ms } = springEasing(springs.snappy);
  assert.match(easing, /^linear\(0, .*1\)$/);
  assert.ok(ms > 0);
});

test("a box transform maps one rectangle onto another", () => {
  const t = boxTransform({ left: 100, top: 50, width: 200, height: 100 }, { left: 0, top: 0, width: 400, height: 400 });
  assert.equal(t, "translate(100px, 50px) scale(0.5, 0.25)");
});

test("a turn: thought, calls, result, answer", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "hello", provider: "x", role: "coder", session: "s1" });
  assert.equal(agent.state, "idle");
  beginTurn(agent);
  feed(
    agent,
    { type: "reasoning_start", stream_id: "t1" },
    { type: "reasoning_chunk", stream_id: "t1", text: "hmm" },
    { type: "reasoning_commit", stream_id: "t1", duration_seconds: 2 },
    { type: "step", step: 1, action_type: "read_file", action_args: { path: "a.py" }, action_description: "reads a.py", call_id: "c1", user_message: "Looking." },
    { type: "action_result", step: 1, call_id: "c1", ok: true, summary: "12 lines", output: "" },
    { type: "step", step: 2, action_type: "write_file", action_args: { path: "b.py" }, call_id: "c2" },
    { type: "action_result", step: 2, call_id: "c2", ok: true, summary: "Wrote 3 lines", path: "b.py", lines_added: 3, lines_removed: 0 },
    { type: "final", message: "Done.", outcome: "done" },
    { type: "turn_end" },
  );
  assert.deepEqual(kinds(agent), ["thought", "reply", "call", "call", "final"]);
  assert.equal(agent.items[0].seconds, 2);
  assert.equal(agent.items[2].phase, "ok");
  assert.deepEqual(agent.changed.get("b.py"), { added: 3, removed: 0, writes: 1 });
  assert.equal(agent.state, "idle");
});

test("a streamed answer is one item that ends settled", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "stream_start", stream_id: "s" }, { type: "stream_chunk", stream_id: "s", text: "He" }, { type: "stream_chunk", stream_id: "s", text: "Hello" });
  assert.equal(agent.items.length, 1);
  assert.equal(agent.items[0].text, "Hello");
  assert.equal(agent.items[0].live, true);
  feed(agent, { type: "stream_commit", stream_id: "s", text: "Hello!" });
  assert.equal(agent.items[0].text, "Hello!");
  assert.equal(agent.items[0].live, false);
});

test("an empty stream_commit draws nothing", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "stream_start", stream_id: "s" }, { type: "stream_commit", stream_id: "s", text: "" });
  assert.equal(agent.items.length, 0);
});

test("a call still running when the turn ends is marked as stopped", () => {
  const agent = createAgent("a1", "/w");
  beginTurn(agent);
  feed(agent, { type: "step", step: 1, action_type: "run_shell", call_id: "c" }, { type: "turn_end" });
  assert.equal(agent.items[0].phase, "fail");
  assert.equal(agent.state, "idle");
});

test("a paused turn leaves the agent paused", () => {
  const agent = createAgent("a1", "/w");
  beginTurn(agent);
  feed(agent, { type: "final", message: "p", outcome: "incomplete", paused: true }, { type: "turn_end" });
  assert.equal(agent.state, "paused");
});

test("a closed session shows its notice and a closing message is not a failure", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "final", message: "Bye.", outcome: "incomplete", closed: true }, { type: "session_ended", closed_notice: "Closed · Enter — new session" });
  assert.equal(agent.items[0].kind, "reply");
  assert.equal(agent.closed, "Closed · Enter — new session");
  assert.equal(summary(agent), "closed");
  feed(agent, { type: "session", session: "chat-2", has_checkpoint: false });
  assert.equal(agent.closed, null);
});

test("dialogs queue and a timeout closes them", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "input_request", request_id: "r1", kind: "confirm", title: "Run?" });
  assert.equal(summary(agent), "asks");
  feed(agent, { type: "input_closed", request_id: "r1", reason: "timeout" });
  assert.equal(agent.dialogs.length, 0);
});

test("helpers keep their live activity, matched by call", () => {
  const agent = createAgent("a1", "/w");
  feed(
    agent,
    { type: "helper", agent_id: "h1", event: "started", state: "running", task: "tests" },
    { type: "helper_activity", agent_id: "h1", phase: "step", call_id: "x", label: "runs pytest" },
    { type: "helper_activity", agent_id: "h1", phase: "result", call_id: "x", ok: true, summary: "ok" },
  );
  const helper = agent.helpers.get("h1");
  assert.equal(helper.task, "tests");
  assert.equal(helper.activity.length, 1);
  assert.equal(helper.activity[0].ok, true);
});

test("the agent's screen appears and goes away", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "screen", screen_id: "b", skill: "browser", state: "running", stream_url: "http://127.0.0.1:1/s" });
  assert.equal(agent.screen.stream_url, "http://127.0.0.1:1/s");
  feed(agent, { type: "screen", screen_id: "b", state: "closed" });
  assert.equal(agent.screen, null);
});

test("a session switch clears the feed and asks for the new history", () => {
  const agent = createAgent("a1", "/w", "s1");
  feed(agent, { type: "message", markdown: "hi" });
  feed(agent, { type: "command_result", name: "session", ok: true, message: "", effects: { session: "s2", changed: true } });
  assert.equal(agent.items.length, 0);
  assert.equal(agent.session, "s2");
  assert.equal(agent.needsHistory, true);
});

test("a journal replays into the same feed without waking the agent", () => {
  const agent = createAgent("a1", "/w");
  agent.state = "asleep";
  replayHistory(agent, [
    { seq: 1, event: { type: "user", text: "fix it" } },
    { seq: 2, event: { type: "step", step: 1, action_type: "run_shell", call_id: "c", action_description: "runs tests" } },
    { seq: 3, event: { type: "action_result", step: 1, call_id: "c", ok: true, summary: "exit 0", exit_code: 0 } },
    { seq: 4, event: { type: "stream_commit", stream_id: "s", text: "Fixed." } },
    { seq: 5, event: { type: "turn_end" } },
  ]);
  assert.deepEqual(kinds(agent), ["user", "call", "reply"]);
  assert.equal(agent.state, "asleep");
});

test("unknown event types change nothing", () => {
  const agent = createAgent("a1", "/w");
  const before = agent.rev;
  reduce(agent, { type: "something_new", x: 1 });
  assert.equal(agent.rev, before);
});

test("formulas are taken out before Markdown and prices stay prices", async () => {
  const { setMathRenderer } = await import("../ui/js/markdown.js");
  assert.match(renderMarkdown("area $a_1 * b_2$ and $$x^2$$"), /<span class="math raw">a_1 \* b_2<\/span>.*<div class="math-block raw">x\^2<\/div>/s);
  assert.ok(!renderMarkdown("costs $5 and $6").includes("math"));
  assert.ok(!renderMarkdown("`$x$` in code").includes('class="math'));
  setMathRenderer((tex, display) => `<k${display ? "d" : "i"}>${tex}</k${display ? "d" : "i"}>`);
  assert.equal(renderMarkdown("\\(a\\) \\[b\\]"), "<p><ki>a</ki> <kd>b</kd></p>");
  setMathRenderer(null);
});

test("the rail: a folder lists its stored sessions and lights the one that is open and in front", () => {
  const open = createAgent("a1", "/work/kvist", "chat-2");
  open.state = "idle";
  open.title = "Fix the parser";
  const tree = buildTree({
    projects: ["/work/kvist", "/work/docs"],
    agents: [open],
    listings: new Map([["/work/kvist", [{ name: "chat-1", title: "Old work" }, { name: "chat-2", title: "stored title" }]]]),
    expanded: ["/work/kvist"],
    activeId: "a1",
  });
  assert.deepEqual(tree.map((p) => [p.name, p.open, p.sessions.length]), [["kvist", true, 2], ["docs", false, 0]]);
  const [old, current] = tree[0].sessions;
  assert.deepEqual([old.agentId, old.state, old.active], [null, null, false], "a stored session with no agent has no core and no state");
  assert.deepEqual([current.agentId, current.state, current.active, current.title], ["a1", "idle", true, "Fix the parser"]);
  assert.equal(tree[0].active, true);
});

test("the rail: a session that is open but not stored yet is listed first, and a folder with an agent appears by itself", () => {
  const fresh = createAgent("a2", "/work/new", "chat-9");
  fresh.state = "running";
  const tree = buildTree({ projects: [], agents: [fresh], listings: new Map(), expanded: [], activeId: null });
  assert.equal(tree.length, 1);
  assert.equal(tree[0].name, "new");
  assert.deepEqual(tree[0].sessions.map((s) => [s.session, s.state]), [["chat-9", "running"]]);
  assert.equal(tree[0].running, 1);
});

test("the rail: a closed session and a waiting question are marked", () => {
  const a = createAgent("a3", "/w", "s");
  a.state = "idle";
  a.closed = "closed";
  const b = createAgent("a4", "/w", "t");
  b.dialogs.push({ request_id: "r" });
  const [project] = buildTree({ projects: ["/w"], agents: [a, b], listings: new Map(), expanded: ["/w"], activeId: null });
  const bySession = Object.fromEntries(project.sessions.map((s) => [s.session, s]));
  assert.equal(bySession.s.closed, true);
  assert.equal(bySession.t.state, "asks");
});

import { shimmerColor, coolDown, spinnerWord, formatSeconds } from "../ui/js/spinner.js";

test("the answer's own step draws no card, and what the model said with it stays", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "step", step: 3, action_type: "final_answer", user_message: "Done.", call_id: "z" }, { type: "final", message: "Done.", outcome: "done" }, { type: "turn_end" });
  assert.deepEqual(kinds(agent), ["reply", "final"].slice(0, kinds(agent).length));
  assert.ok(!kinds(agent).includes("call"), "no card that would be marked as stopped");
});

test("a turn really stopped midway still marks its running call as stopped", () => {
  const agent = createAgent("a1", "/w");
  beginTurn(agent);
  feed(agent, { type: "step", step: 1, action_type: "run_shell", call_id: "c" }, { type: "turn_end" });
  assert.equal(agent.items[0].phase, "fail");
});

test("the spinner's colour starts blue, warms with the wait and rises at once but cools slowly", () => {
  const blue = shimmerColor(0, 0, 0);
  const hot = shimmerColor(60, 0, 0);
  assert.match(blue, /^#[0-9a-f]{6}$/);
  const red = (hexColor) => parseInt(hexColor.slice(1, 3), 16);
  const blueChannel = (hexColor) => parseInt(hexColor.slice(5, 7), 16);
  assert.ok(red(hot) > red(blue) && blueChannel(hot) < blueChannel(blue));
  assert.equal(coolDown(5, 12, 0.1), 12, "a longer wait shows at once");
  const cooled = coolDown(60, 0, 1);
  assert.ok(cooled > 20 && cooled < 25, "it falls back gradually, not in one step");
});

test("the spinner's word follows the role and the language of the request", () => {
  assert.equal(spinnerWord("chat", 0, 0, "hello"), "Generating a response");
  assert.equal(spinnerWord("chat", 0, 0, "привет"), "Генерирую ответ");
  assert.equal(spinnerWord("auto", 0, 0, "x"), "Picking a role");
  assert.notEqual(spinnerWord("coder", 0, 0, "x"), spinnerWord("coder", 0, 9, "x"), "the word changes as the turn goes on");
  assert.equal(formatSeconds(75), "1m 15s");
});

import { activate, restoreTab, wake } from "../ui/js/actions.js";
import { store, subscribe as onChange } from "../ui/js/store.js";

test("a stored session shows its history even though showing it wakes its agent at once", async () => {
  // The chat view wakes whatever it is switched to; that used to hide the "asleep" state the history load looked for.
  const off = onChange((change) => {
    if (change.kind === "active") wake(store.agents.get(store.activeId));
  });
  const agent = restoreTab({ workspace: "/work/kvist", session: "older-work", title: "Docs cleanup" });
  await activate(agent.id);
  off();
  assert.equal(agent.state, "starting", "it was woken");
  assert.deepEqual(kinds(agent), ["user", "reply"], "and its earlier conversation is there, not an empty page");
  assert.equal(agent.items[0].text, "Earlier request");
});
