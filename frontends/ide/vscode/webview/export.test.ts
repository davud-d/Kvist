import assert from "node:assert/strict"
import { test } from "node:test"

import type { ActionResult, Item } from "../../../tui/ink/state"
import { answerToText, turnSlice, turnToText } from "./export"

let nextId = 1
const id = () => nextId++

const user = (text: string): Item => ({ id: id(), kind: "user", text })
const answer = (text: string): Item => ({ id: id(), kind: "answer", text, done: true })
const result = (fields: Partial<ActionResult> = {}): ActionResult => ({
  ok: true,
  summary: "ok",
  output: "",
  exit_code: null,
  path: null,
  first_line: null,
  ...fields,
})
const tool = (action: string, args: Record<string, unknown>, res: Partial<ActionResult> = {}): Item => ({
  id: id(),
  kind: "tool",
  step: 1,
  planStep: "edit",
  action,
  args,
  result: result(res),
})

test("turnSlice takes everything from the nearest preceding user item to the answer, inclusive", () => {
  const items = [user("first"), answer("one"), user("second"), tool("run_shell", { command: ["ls"] }), answer("two")]
  const slice = turnSlice(items, items[4]!.id)
  assert.deepEqual(slice.map((i) => i.kind), ["user", "tool", "answer"])
  assert.equal((slice[0] as Extract<Item, { kind: "user" }>).text, "second")
})

test("turnSlice falls back to the start of the feed when there is no preceding user item", () => {
  const items = [answer("resumed answer")]
  const slice = turnSlice(items, items[0]!.id)
  assert.deepEqual(slice, items)
})

test("turnSlice returns nothing for an id that is not present", () => {
  assert.deepEqual(turnSlice([user("x")], 999), [])
})

test("turnToText keeps a proposed plan as its own Markdown under a labelled heading", () => {
  const plan = (russian: boolean): Item => ({ id: id(), kind: "plan", markdown: "- rename `a`\n- run the tests", russian })
  assert.equal(turnToText([plan(false)]), "**Proposed plan**\n\n- rename `a`\n- run the tests")
  assert.match(turnToText([plan(true)]), /^\*\*Предложенный план\*\*/)
})

test("answerToText is just the trimmed answer text", () => {
  assert.equal(answerToText({ id: 1, kind: "answer", text: "  Done.  \n", done: true }), "Done.")
})

test("turnToText renders the request, a write, and the answer as readable markdown", () => {
  const items: Item[] = [
    user("add a helper"),
    tool("write_file", { path: "a.py", content: "x = 1\n" }, { path: "a.py", first_line: 1, summary: "Wrote 1 line" }),
    answer("Done."),
  ]
  const text = turnToText(items)
  assert.match(text, /\*\*You:\*\* add a helper/)
  assert.match(text, /\*\*Wrote `a\.py`\*\*/)
  assert.match(text, /```diff\nx = 1\n```/)
  assert.match(text, /Done\.$/)
})

test("turnToText renders a shell command with its output and exit code", () => {
  const items: Item[] = [
    tool("run_shell", { command: ["pytest", "-q"] }, { output: "14 passed", exit_code: 0, summary: "Command exited with code 0" }),
  ]
  const text = turnToText(items)
  assert.match(text, /\$ pytest -q/)
  assert.match(text, /14 passed/)
  assert.match(text, /exit 0/)
})

test("turnToText renders an edit as a unified-looking diff", () => {
  const items: Item[] = [
    tool(
      "replace_in_file",
      { path: "a.py", old_text: "x = 1", new_text: "x = 2" },
      { path: "a.py", first_line: 1, summary: "Replaced 1 occurrence" },
    ),
  ]
  const text = turnToText(items)
  assert.match(text, /-x = 1/)
  assert.match(text, /\+x = 2/)
})

test("turnToText marks a failed action instead of pretending it succeeded", () => {
  const items: Item[] = [tool("run_shell", { command: ["pytest"] }, { ok: false, summary: "Command exited with code 1", exit_code: 1 })]
  const text = turnToText(items)
  assert.match(text, /exit 1/)
})

test("turnToText drops debug notes but keeps other notes and skips empty items", () => {
  const items: Item[] = [
    { id: id(), kind: "note", tone: "debug", text: "verbose noise" },
    { id: id(), kind: "note", tone: "error", text: "something failed" },
    { id: id(), kind: "banner" },
  ]
  const text = turnToText(items)
  assert.ok(!text.includes("verbose noise"))
  assert.match(text, /something failed/)
})

test("turnToText includes a thought's text and an explored summary", () => {
  const items: Item[] = [
    { id: id(), kind: "thought", seconds: 2.4, text: "checking the cache" },
    { id: id(), kind: "explored", counts: { reads: 2, searches: 0, lists: 0, git: 0 }, names: [] },
  ]
  const text = turnToText(items)
  assert.match(text, /checking the cache/)
  assert.match(text, /Explored: 2 reads/)
})
