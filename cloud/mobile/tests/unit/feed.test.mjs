import assert from "node:assert/strict";
import { test } from "node:test";
import { actionKind, createFeed, reduce, tally, working } from "../../js/feed.js";

const run = (events, options) => {
  const feed = createFeed();
  for (const event of events) reduce(feed, event, options);
  return feed;
};

test("steps between two messages become one work item", () => {
  const feed = run([
    { type: "user", text: "fix it" },
    { type: "step", step: 1, call_id: "a", action_type: "read_file", action_description: "Read a.py" },
    { type: "action_result", call_id: "a", ok: true, summary: "12 lines" },
    { type: "step", step: 2, call_id: "b", action_type: "write_file", action_description: "Write a.py" },
    { type: "action_result", call_id: "b", ok: true, path: "a.py", lines_added: 3, lines_removed: 1 },
    { type: "stream_chunk", stream_id: "s", text: "Done", append: true },
    { type: "stream_commit", stream_id: "s", text: "Done." },
  ]);
  assert.deepEqual(feed.items.map((i) => i.kind), ["user", "work", "reply"]);
  const work = feed.items[1];
  assert.equal(work.live, false);
  assert.deepEqual(tally(work), { total: 2, done: 2, failed: 0, running: null, files: 1 });
  assert.equal(feed.items[2].text, "Done.");
});

test("a streaming reply grows in place and keeps its item", () => {
  const feed = run([{ type: "stream_chunk", stream_id: "s", text: "He", append: true }]);
  const item = feed.items[0];
  const before = item.rev;
  reduce(feed, { type: "stream_chunk", stream_id: "s", text: "llo", append: true });
  assert.equal(feed.items.length, 1);
  assert.equal(item.text, "Hello");
  assert.ok(item.rev > before);
  assert.equal(item.live, true);
});

test("a final answer that repeats the last reply is shown once", () => {
  const feed = run([
    { type: "stream_commit", stream_id: "s", text: "All done." },
    { type: "final", message: "All done.", outcome: "done" },
  ]);
  assert.equal(feed.items.filter((i) => i.kind === "reply").length, 1);
});

test("the end of a turn stops what was still running", () => {
  const feed = run([
    { type: "step", step: 1, call_id: "a", action_type: "run_command", action_description: "npm test" },
    { type: "turn_end" },
  ]);
  const [call] = feed.items[0].calls;
  assert.equal(call.phase, "fail");
  assert.equal(feed.items[0].live, false);
});

test("terminal actions are not steps and the runner's own commands are not news", () => {
  const feed = run([
    { type: "step", step: 1, action_type: "final_answer" },
    { type: "command_result", name: "session", message: "switched" },
    { type: "command_result", name: "memory", message: "forgot it" },
  ]);
  assert.deepEqual(feed.items.map((i) => i.kind), ["note"]);
});

test("a question stays open until answered, closed, or the state moves on", () => {
  const feed = run([{ type: "input_request", request_id: "r1", kind: "confirm", title: "Go?" }]);
  assert.equal(feed.dialogs.length, 1);
  assert.equal(working(feed), "waiting");
  reduce(feed, { type: "input_request", request_id: "r1", kind: "confirm", title: "Go?" });
  assert.equal(feed.dialogs.length, 1, "the same request is the same dialog");
  reduce(feed, { type: "input_closed", request_id: "r1" });
  assert.equal(feed.dialogs.length, 0);
  reduce(feed, { type: "input_request", request_id: "r2", kind: "confirm" });
  reduce(feed, { type: "task_state", state: "running" });
  assert.equal(feed.dialogs.length, 0);
});

test("the server's task status decides whether it is working", () => {
  const feed = createFeed();
  assert.equal(working(feed), "");
  feed.task = { status: "running" };
  assert.equal(working(feed), "working");
  feed.task = { status: "waiting_for_user" };
  assert.equal(working(feed), "waiting");
  feed.task = { status: "done" };
  assert.equal(working(feed), "");
});

test("replayed history does not open calls it can no longer finish", () => {
  const feed = run([{ type: "call_begin", call_id: "x", tool: "read_file", description: "x" }], { replay: true });
  assert.equal(feed.items.length, 0);
});

test("action kinds", () => {
  assert.equal(actionKind("run_command"), "terminal");
  assert.equal(actionKind("replace_in_file"), "edit");
  assert.equal(actionKind("read_file"), "file");
  assert.equal(actionKind("web_fetch"), "globe");
  assert.equal(actionKind("whatever"), "step");
});
