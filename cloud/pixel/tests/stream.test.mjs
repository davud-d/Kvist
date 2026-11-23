// A file the agent is writing, as call_stream pieces arrive, and the colours its lines get. No DOM.
import assert from "node:assert/strict";
import test from "node:test";

import { familyOf, highlightLine } from "../js/highlight.js";
import { createAgent, reduce } from "../js/reducer.js";

const piece = (line, text) => ({ type: "call_stream", call_id: "w1", tool: "write_file", path: "notes.md", field: "content", line, text, done: false });

function written(pieces) {
  const agent = createAgent("a1", "1");
  for (const event of pieces) reduce(agent, event);
  return agent.items.find((item) => item.kind === "call").stream.content;
}

test("the words of the first line add up instead of starting over", () => {
  assert.equal(written([piece(0, "# Cloud "), piece(0, "notes\n\nFirst "), piece(2, "line\n")]), "# Cloud notes\n\nFirst line\n");
});

test("line 0 after the first line is a retried request and starts the field over", () => {
  assert.equal(written([piece(0, "one\ntwo\n"), piece(0, "again\n")]), "again\n");
});

test("a piece that does not follow on is dropped", () => {
  assert.equal(written([piece(0, "a\n"), piece(5, "far\n")]), "a\n");
});

test("code is coloured by its language and everything else stays escaped text", () => {
  assert.equal(familyOf("app/main.py"), "py");
  assert.equal(familyOf("notes.md"), "");
  assert.equal(highlightLine("def total(x): # sum", "py"), '<span class="tk-kw">def</span> <span class="tk-fn">total</span>(x): <span class="tk-com"># sum</span>');
  assert.equal(highlightLine('x = "<b>"', "js"), 'x = <span class="tk-str">&quot;&lt;b&gt;&quot;</span>');
  assert.equal(highlightLine("<script>", ""), "&lt;script&gt;");
});

test("what the agent did between two messages is one block of work", async () => {
  const { feedBlocks, workSummary } = await import("../js/feed.js");
  const items = [
    { id: 1, kind: "user", rev: 1 },
    { id: 2, kind: "thought", rev: 1, text: "which file?", live: false },
    { id: 3, kind: "call", rev: 2, phase: "ok", args: { path: "a.py" }, patch: "+x" },
    // A thought the provider kept to itself: nothing to show, and the work around it stays one block.
    { id: 7, kind: "thought", rev: 1, text: "", live: false },
    { id: 4, kind: "call", rev: 1, phase: "fail", args: {} },
    { id: 5, kind: "reply", rev: 1 },
    { id: 6, kind: "note", tone: "error", rev: 1 },
  ];
  const blocks = feedBlocks(items);
  // The thought stands on its own in the conversation, as in Kvist Glass.
  assert.deepEqual(blocks.map((b) => b.kind), ["user", "thought", "work", "reply", "note"]);
  assert.equal(blocks[2].rev, "3.2,4.1");
  assert.deepEqual(workSummary(blocks[2].items), { actions: 2, failed: 1, files: ["a.py"], running: false });
});

test("a thought read back from the history keeps its words and can be opened again", async () => {
  const { feedBlocks } = await import("../js/feed.js");
  const agent = createAgent("a1", "1");
  for (const event of [
    { type: "user", text: "hi" },
    { type: "reasoning_start", stream_id: "s1" },
    { type: "reasoning_commit", stream_id: "s1", duration_seconds: 4, text: "The person says hi." },
  ]) reduce(agent, event, { replay: true });
  const thought = feedBlocks(agent.items).find((block) => block.kind === "thought");
  assert.equal(thought.text, "The person says hi.");
  assert.equal(thought.seconds, 4);
});
