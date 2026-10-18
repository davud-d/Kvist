import assert from "node:assert/strict"
import { test } from "node:test"

import { CLASSIC_COMMAND, COMMANDS, routeOf, sessionArgs } from "../commands.js"
import {
  DIALOG_ROWS,
  choiceWindow,
  counterLabel,
  filterOptions,
  initialChoice,
  pasteChoice,
  pasteLine,
  stepChoice,
  stepConfirm,
  stepLine,
  type ChoiceModel,
  type DialogKey,
} from "../dialog.js"
import { emptyEditor } from "../editor.js"
import type { InputOption } from "../protocol.js"

const options: InputOption[] = Array.from({ length: 45 }, (_, i) => {
  const n = String(i + 1).padStart(2, "0")
  return { value: `m${n}`, label: `Model-${n}`, description: i % 10 === 0 ? `Flagship tier ${n}` : `standard ${n}` }
})

function press(model: ChoiceModel, input: string, key: DialogKey = {}) {
  return stepChoice(model, options, DIALOG_ROWS, input, key)
}

function typeText(model: ChoiceModel, text: string): ChoiceModel {
  return [...text].reduce((acc, char) => press(acc, char).model, model)
}

test("filter matches label and description, any case, every word", () => {
  assert.equal(filterOptions(options, "").length, 45)
  assert.deepEqual(filterOptions(options, "MODEL-07").map((o) => o.value), ["m07"])
  // "flagship" lives only in descriptions: items 1, 11, 21, 31, 41.
  assert.deepEqual(filterOptions(options, "flagship").map((o) => o.value), ["m01", "m11", "m21", "m31", "m41"])
  assert.deepEqual(filterOptions(options, "flag 21").map((o) => o.value), ["m21"])
  assert.equal(filterOptions(options, "nothing like this").length, 0)
})

test("the current value starts under the cursor; an unknown one starts at the top", () => {
  assert.equal(initialChoice(options, "m30").selected, 29)
  assert.equal(initialChoice(options, "missing").selected, 0)
  assert.equal(initialChoice(options, "").selected, 0)
})

test("the window always contains the selection, wherever it goes", () => {
  for (let selected = 0; selected < 45; selected++) {
    const { start, end } = choiceWindow(45, selected, DIALOG_ROWS)
    assert.ok(selected >= start && selected < end, `row ${selected} must be visible`)
    assert.equal(end - start, DIALOG_ROWS)
  }
  assert.deepEqual(choiceWindow(5, 4, DIALOG_ROWS), { start: 0, end: 5 })
  assert.deepEqual(choiceWindow(0, 0, DIALOG_ROWS), { start: 0, end: 0 })
})

test("arrows wrap around and page keys clamp", () => {
  const top = initialChoice(options, "m01")
  assert.equal(press(top, "", { upArrow: true }).model.selected, 44)
  assert.equal(press(press(top, "", { upArrow: true }).model, "", { downArrow: true }).model.selected, 0)
  assert.equal(press(top, "", { pageDown: true }).model.selected, DIALOG_ROWS)
  assert.equal(press(top, "", { pageUp: true }).model.selected, 0)
  assert.equal(press(initialChoice(options, "m45"), "", { pageDown: true }).model.selected, 44)
})

test("walking to the end of a long list keeps a valid selection", () => {
  let model = initialChoice(options, "m01")
  for (let i = 0; i < 44; i++) model = press(model, "", { downArrow: true }).model
  assert.equal(model.selected, 44)
  assert.equal(press(model, "", { return: true }).done?.value, "m45")
})

test("typing filters, resets the selection and Enter picks the highlighted match", () => {
  let model = initialChoice(options, "m30")
  model = typeText(model, "tier 3")
  assert.equal(model.selected, 0)
  // Only the flagship line has "tier"; "tier 3" matches item 31 alone.
  assert.equal(press(model, "", { return: true }).done?.value, "m31")
  model = press(typeText(initialChoice(options, "m01"), "standard"), "", { downArrow: true }).model
  assert.equal(model.selected, 1)
  assert.equal(press(model, "", { return: true }).done?.value, "m03")
})

test("moving the cursor inside the query does not reset the selection", () => {
  let model = typeText(initialChoice(options, "m01"), "standard")
  model = press(model, "", { downArrow: true }).model
  model = press(model, "", { leftArrow: true }).model
  assert.equal(model.selected, 1)
})

test("Enter with no match does nothing; Esc cancels with null", () => {
  const none = typeText(initialChoice(options, "m01"), "zzz")
  const result = press(none, "", { return: true })
  assert.equal(result.done, undefined)
  assert.equal(press(none, "", { downArrow: true }).model.selected, 0)
  assert.deepEqual(press(none, "", { escape: true }).done, { value: null })
})

test("keys that arrive in one chunk are all applied, in order", () => {
  const typed = press(initialChoice(options, "m01"), "flag 21\r")
  assert.equal(typed.done?.value, "m21")
})

test("a paste is trimmed and never acts as Enter", () => {
  const model = pasteChoice(initialChoice(options, "m30"), "  model-07\n")
  assert.equal(model.query.text, "model-07")
  assert.equal(model.selected, 0)
})

test("counter shows the position in what is left after filtering", () => {
  assert.equal(counterLabel(45, 2), "3/45")
  assert.equal(counterLabel(0, 0), "0/0")
})

test("text dialog: empty Enter takes the default, typed text wins, Esc cancels", () => {
  assert.equal(stepLine(emptyEditor, "default", "", { return: true }).done?.value, "default")
  const typed = stepLine(emptyEditor, "default", "abc", {}).editor
  assert.equal(typed.text, "abc")
  assert.equal(stepLine(typed, "default", "", { return: true }).done?.value, "abc")
  assert.equal(stepLine(typed, "default", "", { escape: true }).done?.value, null)
  assert.equal(stepLine(emptyEditor, "", "key\r", {}).done?.value, "key")
  assert.equal(pasteLine(emptyEditor, "sk-123\n").text, "sk-123")
})

test("confirm denies by default: only y approves", () => {
  assert.equal(stepConfirm("y", {}), true)
  assert.equal(stepConfirm("Y", {}), true)
  assert.equal(stepConfirm("", { return: true }), false)
  assert.equal(stepConfirm("", { escape: true }), false)
  assert.equal(stepConfirm("n", {}), false)
  assert.equal(stepConfirm("x", {}), null)
})

test("every command routes to the core or runs here; none is left to the classic terminal", () => {
  // Every wizard the core serves over the `command` message (UI_API.md §8c).
  const core = COMMANDS.filter((c) => c.route === "core").map((c) => c.name)
  for (const name of ["provider", "change-api", "settings-api", "del-provider", "full-reset", "resume", "revert", "strime"]) {
    assert.ok(core.includes(name), `${name} must run in the core`)
  }
  assert.deepEqual(COMMANDS.filter((c) => c.route === "classic"), [])
  assert.equal(routeOf("models"), "core")
  assert.equal(routeOf("quit"), "local")
  assert.equal(routeOf("nope"), null)
  // Kept for the fallback note when a core predating these commands refuses one.
  assert.match(CLASSIC_COMMAND, /--frontend terminal/)
  assert.equal(new Set(COMMANDS.map((c) => c.name)).size, COMMANDS.length)
})

test("/session: a bare name means switch, explicit verbs pass through", () => {
  assert.deepEqual(sessionArgs([]), [])
  assert.deepEqual(sessionArgs(["work"]), ["switch", "work"])
  assert.deepEqual(sessionArgs(["delete", "old"]), ["delete", "old"])
  assert.deepEqual(sessionArgs(["NEW", "x"]), ["NEW", "x"])
})

test("/tui offers every interface, the running one is among them, and the values are what the launcher saves", async () => {
  const { FRONTENDS, frontendOf } = await import("../commands.js")
  const values: string[] = FRONTENDS.map((f) => f.value)
  assert.ok(values.includes(frontendOf("ink")))
  assert.ok(values.includes(frontendOf("claude")))
  assert.deepEqual(values, ["tui", "claude", "glass", "voice", "terminal"])
  assert.equal(new Set(values).size, values.length)
})
