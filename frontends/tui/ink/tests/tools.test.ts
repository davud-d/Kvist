import assert from "node:assert/strict"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import type { ActionResult, Item } from "../state.js"
import { ToolCard } from "../ui/tools.js"

type Tool = Extract<Item, { kind: "tool" }>

const ok: ActionResult = { ok: true, summary: "", output: "", exit_code: null, path: null, first_line: null }

function frame(action: string, args: Record<string, unknown>, result: Partial<ActionResult>): string {
  const item: Tool = { id: 1, kind: "tool", step: 1, planStep: "edit", action, args, result: { ...ok, ...result } }
  const out = render(createElement(ToolCard, { item })).lastFrame() ?? ""
  return out.replace(/\x1b\[[0-9;]*m/g, "")
}

test("write_file lists numbered lines and counts the rest", () => {
  const content = Array.from({ length: 20 }, (_, i) => `row${i + 1}`).join("\n")
  const out = frame("write_file", { path: "a.py", content }, { path: "a.py", first_line: 1 })
  assert.match(out, /# Wrote a\.py/)
  assert.match(out, /\b12 row12/)
  assert.doesNotMatch(out, /row13/)
  assert.match(out, /… 8 more lines/)
})

test("replace_in_file shows removed and added lines from first_line", () => {
  const out = frame("replace_in_file", { path: "a.py", old_text: "x = 1", new_text: "x = 2" }, { path: "a.py", first_line: 17 })
  assert.match(out, /← Edit a\.py/)
  assert.match(out, /17 − x = 1/)
  assert.match(out, /17 \+ x = 2/)
})

test("a failed replace shows the error and no diff", () => {
  const out = frame("replace_in_file", { path: "a.py", old_text: "x", new_text: "y" }, { ok: false, summary: "Failed: not found", output: "not found", path: "a.py" })
  assert.match(out, /✗ Failed: not found/)
  assert.doesNotMatch(out, /−|\+ y/)
})

test("run_shell shows the command, the output and a non-zero exit", () => {
  const out = frame("run_shell", { command: ["pytest", "-q"] }, { ok: false, summary: "Command exited with code 2", output: "1 failed", exit_code: 2 })
  assert.match(out, /\$ pytest -q/)
  assert.match(out, /1 failed/)
  assert.match(out, /Command exited with code 2/)
  assert.match(frame("run_shell", { command: "true" }, { exit_code: 0 }), /\(no output\)/)
})

test("run_shell output is cut to 15 lines", () => {
  const output = Array.from({ length: 40 }, (_, i) => `out${i + 1}`).join("\n")
  const out = frame("run_shell", { command: "make" }, { output, exit_code: 0 })
  assert.match(out, /out15/)
  assert.doesNotMatch(out, /out16/)
  assert.match(out, /… 25 more lines/)
})

test("web_search is one line with its summary", () => {
  assert.match(frame("web_search", { query: "q" }, { summary: "5 results" }), /● web_search\s+5 results/)
})
