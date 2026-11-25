import assert from "node:assert/strict";
import { test } from "node:test";
import { createFeed, feedActivity, reduce, working } from "../../js/feed.js";
import { feedBlocks, workSummary } from "../../js/web/feed.js";

const run = (events, options) => {
  const feed = createFeed();
  for (const event of events) reduce(feed, event, options);
  return feed;
};

const call = (step, type, label) => [
  { type: "step", step, call_id: `c${step}`, action_type: type, action_description: label, action_args: { path: "a.py" } },
  { type: "action_result", call_id: `c${step}`, ok: true, summary: "ok", path: type === "write_file" ? "a.py" : undefined, lines_added: type === "write_file" ? 3 : undefined },
];

test("as on the web: a thought stands on its own, the calls after it are one block, the answer after that", () => {
  const feed = run([
    { type: "user", text: "fix it" },
    { type: "reasoning_chunk", stream_id: "t", text: "Read first.", append: true },
    { type: "reasoning_commit", stream_id: "t", duration_seconds: 2 },
    ...call(1, "read_file", "Read a.py"),
    ...call(2, "write_file", "Write a.py"),
    { type: "stream_chunk", stream_id: "s", text: "Done", append: true },
    { type: "stream_commit", stream_id: "s", text: "Done." },
  ]);
  assert.deepEqual(feed.items.map((i) => i.kind), ["user", "thought", "call", "call", "reply"]);
  const blocks = feedBlocks(feed.items);
  assert.deepEqual(blocks.map((b) => b.kind), ["user", "thought", "work", "reply"]);
  const summary = workSummary(blocks[2].items);
  assert.equal(summary.actions, 2);
  assert.deepEqual(summary.files, ["a.py"]);
  assert.equal(summary.running, false);
});

test("a thought between calls splits them into two blocks", () => {
  const feed = run([
    ...call(1, "read_file", "a"),
    { type: "reasoning_chunk", stream_id: "t", text: "Hm.", append: true },
    { type: "reasoning_commit", stream_id: "t" },
    ...call(2, "read_file", "b"),
  ]);
  assert.deepEqual(feedBlocks(feed.items).map((b) => b.kind), ["work", "thought", "work"]);
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
  assert.equal(feed.items.length, 1);
});

test("the end of a turn stops what was still running", () => {
  const feed = run([
    { type: "step", step: 1, call_id: "a", action_type: "run_command", action_description: "npm test" },
    { type: "turn_end" },
  ]);
  assert.equal(feed.items[0].phase, "fail");
});

test("terminal actions are not calls and the runner's own commands are not news", () => {
  const feed = run([
    { type: "step", step: 1, action_type: "final_answer" },
    { type: "command_result", name: "session", message: "switched" },
    { type: "command_result", name: "memory", ok: true, message: "forgot it" },
  ]);
  assert.deepEqual(feed.items.map((i) => i.kind), ["reply"]);
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

test("what the orb shows: the running call, or that the words are being written", () => {
  const feed = run([{ type: "user", text: "go" }, { type: "step", step: 1, call_id: "a", action_type: "read_file", action_description: "a" }]);
  feed.task = { status: "running" };
  assert.equal(feedActivity(feed).running.actionType, "read_file");
  reduce(feed, { type: "action_result", call_id: "a", ok: true });
  reduce(feed, { type: "stream_chunk", stream_id: "s", text: "Hi", append: true });
  assert.equal(feedActivity(feed).running, null);
  assert.equal(feedActivity(feed).speaking, true);
});

test("replayed history does not open calls it can no longer finish", () => {
  const feed = run([{ type: "call_begin", call_id: "x", tool: "read_file", description: "x" }], { replay: true });
  assert.equal(feed.items.length, 0);
});
