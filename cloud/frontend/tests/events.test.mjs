import assert from "node:assert/strict";
import { test } from "node:test";
import { apply, blocks, initialFeed, inline } from "../src/js/events.js";

function run(events) {
  const feed = initialFeed();
  for (const event of events) apply(feed, event);
  return feed;
}

test("one model response is one group: thought, words, calls and results", () => {
  const feed = run([
    { type: "reasoning_start", stream_id: "s1" },
    { type: "reasoning_chunk", stream_id: "s1", text: "look at tests" },
    { type: "reasoning_commit", stream_id: "s1", duration_seconds: 4 },
    { type: "step", step: 1, batch_id: "b1", call_id: "c1", action_type: "run_shell",
      action_description: "runs the tests", action_args: { command: ["pytest"] }, user_message: "Running tests" },
    { type: "action_output", step: 1, call_id: "c1", output: "collecting" },
    { type: "step", step: 2, batch_id: "b1", call_id: "c2", action_type: "read_file", action_description: "reads a.py" },
    { type: "action_result", step: 1, call_id: "c1", ok: false, summary: "Command exited with code 1", output: "1 failed" },
    { type: "action_result", step: 2, call_id: "c2", ok: true, summary: "40 lines" },
  ]);
  assert.equal(feed.groups.length, 1);
  const [group] = feed.groups;
  assert.deepEqual(group.reasoning, { text: "look at tests", seconds: 4, live: false });
  assert.equal(group.say, "Running tests");
  assert.deepEqual(group.actions.map((a) => [a.callId, a.running, a.result.ok]), [["c1", false, false], ["c2", false, true]]);
  assert.equal(group.actions[0].live, "collecting");
});

test("the next response opens the next group", () => {
  const feed = run([
    { type: "step", step: 1, batch_id: "b1", call_id: "c1", action_type: "read_file" },
    { type: "action_result", step: 1, call_id: "c1", ok: true },
    { type: "reasoning_start", stream_id: "s2" },
    { type: "step", step: 2, batch_id: "b2", call_id: "c2", action_type: "write_file" },
    { type: "step", step: 3, batch_id: "b3", call_id: "c3", action_type: "write_file" },
  ]);
  assert.deepEqual(feed.groups.map((g) => [g.n, g.batch, g.actions.length]), [[1, "b1", 1], [2, "b2", 1], [3, "b3", 1]]);
  assert.ok(feed.groups[1].reasoning);
});

test("call_begin cards are filled in by their step", () => {
  const feed = run([
    { type: "call_begin", call_id: "c1", tool: "write_file", path: "a.py", description: "writes a.py" },
    { type: "call_begin", call_id: "c2", tool: "write_file", path: "b.py", description: "writes b.py" },
    { type: "step", step: 1, batch_id: "b1", call_id: "c1", action_type: "write_file", action_description: "writes a.py" },
    { type: "step", step: 2, batch_id: "b1", call_id: "c2", action_type: "write_file", action_description: "writes b.py" },
  ]);
  assert.equal(feed.groups.length, 1);
  assert.deepEqual(feed.groups[0].actions.map((a) => a.callId), ["c1", "c2"]);
});

test("a question for the person is held until answered or closed", () => {
  const feed = run([{ type: "input_request", request_id: "r1", kind: "confirm", title: "Run it?" }]);
  assert.equal(feed.input.request_id, "r1");
  apply(feed, { type: "input_closed", request_id: "r1" });
  assert.equal(feed.input, null);
});

test("final and turn_end settle the feed", () => {
  const feed = run([
    { type: "step", step: 1, batch_id: "b1", call_id: "c1", action_type: "run_shell" },
    { type: "final", message: "Done", outcome: "done" },
    { type: "turn_end" },
  ]);
  assert.equal(feed.final.message, "Done");
  assert.equal(feed.groups[0].actions[0].running, false);
  assert.equal(feed.open, null);
});

test("unknown events change nothing", () => {
  const feed = run([{ type: "something_new", x: 1 }, { type: "usage" }]);
  assert.equal(feed.groups.length, 0);
});

test("markdown blocks and inline code", () => {
  assert.deepEqual(blocks("Hello\nworld\n\n```\ncode\n```\nend"), [
    { kind: "p", text: "Hello\nworld" }, { kind: "code", text: "code" }, { kind: "p", text: "end" },
  ]);
  assert.deepEqual(inline("run `pytest` now"), [["text", "run "], ["code", "pytest"], ["text", " now"]]);
});
