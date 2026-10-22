import assert from "node:assert/strict"
import { test } from "node:test"

import { FRAME_FILL, sampleBackdrop, sampleCells, sampleFramed } from "../setup/backdrop.js"
import { detectColorLevel, toXterm256 } from "../setup/color.js"
import { choiceRows, chosenComponents, initialState, update, type Action, type SetupState, type Update } from "../setup/flow.js"
import { parseInput } from "../setup/keys.js"
import { pictureSize, renderPicture } from "../setup/picture.js"
import { introFrame, shortPath } from "../setup/run.js"
import { DIMMED, MIN_COLS, MIN_ROWS, renderScene } from "../setup/scene.js"
import { detectLocale, stringsFor } from "../setup/strings.js"

const strings = stringsFor("en")
const plain = (text: string) => text.replace(/\x1b\[[0-9;]*m/g, "")

const providers = ["anymodel", "chatgpt", "openai", "openrouter"].map((id) => ({
  id,
  label: id,
  auth_type: id === "chatgpt" ? ("oauth" as const) : ("api_key" as const),
  requires_manual_models: id === "anymodel" || id === "openrouter",
  has_credentials: false,
}))

const ready = (): SetupState => ({ ...initialState(), catalog: "ready", providers })
const key = (name: string, text = ""): Action => ({ type: "input", event: { type: "key", key: name as never, text, ctrl: false } })
const typed = (state: SetupState, text: string) => [...text].reduce((s, ch) => update(s, key("char", ch), strings).state, state)
const send = (state: SetupState, action: Action): Update => update(state, action, strings)

test("keys: arrows, mouse reports, paste and a split escape sequence", () => {
  const parsed = parseInput("\x1b[A\x1b[<0;12;5M\x1b[<64;1;1M\x1b[200~sk-abc\n\x1b[201~x")
  assert.deepEqual(
    parsed.events.map((event) => (event.type === "key" ? event.key : event.type === "paste" ? `paste:${event.text}` : `${event.kind}:${event.col},${event.row}`)),
    ["up", "press:11,4", "wheel:0,0", "paste:sk-abc\n", "char"],
  )
  // Motion with no button held (35) and with the left one held (32) are both "move".
  assert.deepEqual(
    parseInput("\x1b[<35;10;4M\x1b[<32;11;4M").events.map((event) => (event.type === "mouse" ? `${event.kind}:${event.col},${event.row}` : event.type)),
    ["move:9,3", "move:10,3"],
  )
  assert.equal(parseInput("\x1b[").rest, "\x1b[")
  assert.equal(parseInput("\x1b").rest, "\x1b")
  assert.equal(parseInput("\x1b", true).events[0]?.type, "key")
  const ctrl = parseInput("\x03").events[0]
  assert.ok(ctrl?.type === "key" && ctrl.ctrl && ctrl.text === "c")
})

test("old-style mouse reports (macOS Terminal.app) are mouse events, not typed characters", () => {
  // ESC [ M, then button, column and row, each 32 higher than its value (column and row 1-based).
  const press = `\x1b[M${String.fromCharCode(32)}${String.fromCharCode(32 + 10)}${String.fromCharCode(32 + 4)}`
  const release = `\x1b[M${String.fromCharCode(35)}${String.fromCharCode(42)}${String.fromCharCode(36)}`
  const wheel = `\x1b[M${String.fromCharCode(32 + 64)}${String.fromCharCode(33)}${String.fromCharCode(33)}`
  const parsed = parseInput(`${press}${release}${wheel}x`)
  assert.deepEqual(
    parsed.events.map((event) => (event.type === "mouse" ? `${event.kind}:${event.col},${event.row}` : event.type === "key" ? event.text : event.type)),
    ["press:9,3", "release:9,3", "wheel:0,0", "x"],
  )
  assert.equal(parseInput("\x1b[M ").rest, "\x1b[M ")
})

test("colour level follows the terminal's own statements", () => {
  assert.equal(detectColorLevel({ COLORTERM: "truecolor", TERM: "xterm" }), "truecolor")
  assert.equal(detectColorLevel({ TERM: "xterm-256color" }), "256")
  assert.equal(detectColorLevel({ TERM: "linux" }), "none")
  assert.equal(detectColorLevel({ COLORTERM: "truecolor", NO_COLOR: "1" }), "none")
  assert.equal(detectColorLevel({ TERM: "xterm-kitty" }), "truecolor")
  assert.equal(toXterm256([255, 255, 255]), 231)
  assert.equal(detectLocale({ LANG: "ru_RU.UTF-8" }), "ru")
  assert.equal(detectLocale({ LANG: "de_DE.UTF-8" }), "en")
})

test("the picture covers any window and is cached per size", () => {
  const grid = sampleBackdrop(100, 60)
  assert.equal(grid.rgb.length, 100 * 60 * 3)
  assert.equal(sampleBackdrop(100, 60), grid)
  // Larger than the source: nearest pixels, no gaps.
  assert.equal(sampleBackdrop(500, 300).rgb.length, 500 * 300 * 3)
})

test("the picture sits inside a darker, blurred surround of itself", () => {
  const framed = sampleFramed(200, 100)
  assert.equal(framed.rgb.length, 200 * 100 * 3)
  assert.equal(sampleFramed(200, 100), framed)
  const sum = (x0: number, x1: number, y: number) => {
    let total = 0
    for (let x = x0; x < x1; x++) for (let c = 0; c < 3; c++) total += framed.rgb[(y * 200 + x) * 3 + c]!
    return total
  }
  // Inside the picture (88% of the window) is brighter than the strip left of it.
  const margin = Math.floor((200 * (1 - FRAME_FILL)) / 2)
  assert.ok(sum(margin + 10, margin + 30, 50) > sum(0, margin - 4, 50) * 1.2)
})

test("cells carry a quadrant glyph and two colours that explain the pixels they stand for", () => {
  const art = sampleCells(40, 12)
  assert.equal(art.chars.length, 40 * 12)
  assert.ok(art.chars.every((char) => /^[ ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█]$/.test(char)))
  assert.equal(sampleCells(40, 12), art)
  // Two colours at most, so a flat area is one glyph with equal-ish colours and an edge is two.
  assert.ok(art.chars.some((char) => char !== " " && char !== "█"))
})

test("a picture size follows the window and never exceeds the source", () => {
  assert.deepEqual(pictureSize(100, 30, { width: 10, height: 20 }), { width: 1000, height: 600 })
  const wide = pictureSize(300, 80, { width: 10, height: 20 })
  assert.equal(wide.width, 1280)
  assert.equal(wide.height, Math.round(80 * 20 * (1280 / 3000)))
  assert.deepEqual(pictureSize(80, 24, null), { width: 800, height: 480 })
})

test("the picture is darkened by the pane only inside the panel", () => {
  const size = { width: 200, height: 100 }
  const plainRgb = renderPicture(size, 20, 10, 1, null)
  const withPane = renderPicture(size, 20, 10, 1, { col: 5, row: 2, width: 10, height: 6, opacity: 1, glass: false })
  const at = (x: number, y: number) => (y * 200 + x) * 3
  assert.equal(withPane[at(5, 5)], plainRgb[at(5, 5)])
  assert.ok(withPane[at(100, 50)]! < 40)
  // A glass pane keeps more of the picture than a dialog's does.
  const glass = renderPicture(size, 20, 10, 1, { col: 5, row: 2, width: 10, height: 6, opacity: 1, glass: true })
  assert.ok(glass[at(100, 50)]! > withPane[at(100, 50)]!)
  // Brightness scales every pixel.
  const dim = renderPicture(size, 20, 10, 0.5, null)
  assert.ok(dim[at(100, 50)]! <= Math.ceil(plainRgb[at(100, 50)]! / 2) + 1)
})

test("a picture shown as an image leaves the cells around the panel empty", () => {
  const scene = renderScene({ state: ready(), strings, cols: 80, rows: 24, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "image" })
  assert.ok(scene.rows.every((row) => !/[▀▄▌▐▘▝▖▗▚▞▛▜▙▟█]/.test(row)))
  assert.ok(scene.panel && scene.panel.width > 40)
  assert.ok(scene.rows.some((row) => plain(row).includes("Welcome to Kvist")))
})

test("a provider with a key leads through the key to the interface choice", () => {
  let state = send(ready(), key("enter")).state
  assert.equal(state.step, "provider")
  state = send(state, key("down")).state // chatgpt
  state = send(state, key("down")).state // openai
  const chosen = send(state, key("enter"))
  assert.equal(chosen.state.step, "key")
  assert.equal(send(chosen.state, key("enter")).state.error, strings.keyEmpty)
  const pasted = send(chosen.state, { type: "input", event: { type: "paste", text: " sk-123\n" } }).state
  assert.equal(pasted.key, "sk-123")
  const submitted = send(pasted, key("enter"))
  assert.equal(submitted.state.step, "working")
  assert.deepEqual(submitted.effect, { type: "connect", provider: "openai", key: "sk-123", model: "" })
  const failed = send(submitted.state, { type: "connected", reply: { ok: false, error: "bad key" } }).state
  assert.deepEqual([failed.step, failed.error, failed.key], ["key", "bad key", "sk-123"])
  const done = send(submitted.state, { type: "connected", reply: { ok: true, provider: "openai", models: {} } }).state
  assert.equal(done.step, "interface")
  assert.deepEqual(send(done, key("enter")).effect, { type: "finish", outcome: { kind: "frontend", value: "claude" } })
})

test("Classic is offered after Claude-style only where the terminal shows pictures", () => {
  const atInterface = (glass: boolean): SetupState => ({ ...ready(), step: "interface", connected: "openai", glass })
  const shown = (state: SetupState) =>
    renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.doesNotMatch(shown(atInterface(false)), /Classic {2,}A photograph/)
  assert.match(shown(atInterface(true)), /2 {2}Classic {2,}A photograph/)
  const second = send(atInterface(true), key("down")).state
  assert.deepEqual(send(second, key("enter")).effect, { type: "finish", outcome: { kind: "frontend", value: "glass" } })
  assert.deepEqual(send(atInterface(true), key("char", "4")).effect, { type: "finish", outcome: { kind: "frontend", value: "opencode" } })
  assert.deepEqual(send(atInterface(true), key("char", "5")).effect, { type: "finish", outcome: { kind: "frontend", value: "terminal" } })
  assert.deepEqual(send(atInterface(false), key("char", "3")).effect, { type: "finish", outcome: { kind: "frontend", value: "opencode" } })
  assert.deepEqual(send(atInterface(false), key("char", "4")).effect, { type: "finish", outcome: { kind: "frontend", value: "terminal" } })
})

test("sessions of other agents are offered after the interface and before Kvist starts", () => {
  const connecting: SetupState = { ...ready(), workspace: "/work/app", step: "working", provider: providers[2]! }
  const connected = send(connecting, { type: "connected", reply: { ok: true, provider: "openai", models: {} } })
  assert.deepEqual(connected.effect, { type: "scanImports", workspace: "/work/app" })
  // Nothing found: the interface choice starts Kvist at once.
  assert.deepEqual(send(connected.state, key("enter")).effect, { type: "finish", outcome: { kind: "frontend", value: "claude" } })

  const found = send(connected.state, { type: "imports", reply: { ok: true, agents: [{ key: "claude-code", label: "Claude Code", here: 3 }, { key: "codex", label: "Codex", here: 1 }] } }).state
  const atImport = send(found, key("char", "2")).state
  assert.equal(atImport.step, "import")
  assert.equal(atImport.chosen, "tui")
  const shown = renderScene({ state: atImport, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.match(shown, /\[x\] +CC +Claude Code +3 sessions/)
  assert.match(shown, /\[x\] +CX +Codex +1 session\b/)

  // Untick Codex, import the rest, then start the chosen interface.
  const unticked = send(atImport, key("char", "2")).state
  const importing = send(unticked, key("enter"))
  assert.equal(importing.state.step, "importing")
  assert.deepEqual(importing.effect, { type: "importSessions", workspace: "/work/app", agents: ["claude-code"] })
  assert.deepEqual(send(importing.state, { type: "imported", reply: { ok: true, imported: 3, message: "" } }).effect, { type: "finish", outcome: { kind: "frontend", value: "tui" } })
  // A failure returns to the step with the reason; skipping starts Kvist without importing.
  const failed = send(importing.state, { type: "imported", reply: { ok: false, error: "locked" } }).state
  assert.deepEqual([failed.step, failed.error], ["import", "locked"])
  assert.deepEqual(send(atImport, key("char", "s")).effect, { type: "finish", outcome: { kind: "frontend", value: "tui" } })
  assert.equal(send(atImport, key("escape")).state.step, "interface")
})

test("an empty key on AnyModel opens registration instead of complaining", () => {
  const state = send(send(ready(), key("enter")).state, key("enter")).state
  assert.equal(state.provider?.id, "anymodel")
  assert.deepEqual(send(state, key("enter")).effect, { type: "register", provider: "anymodel" })
  const registered = send(state, { type: "registered", reply: { ok: true, opened: true, url: "https://x" } }).state
  assert.equal(send(registered, key("enter")).state.error, strings.keyEmpty)
})

test("a provider without default models picks one from the live catalog or types an id", () => {
  let state = send(send(ready(), key("enter")).state, key("enter")).state
  state = typed(state, "sk-any")
  const listing = send(state, key("enter"))
  assert.deepEqual(listing.effect, { type: "listModels", provider: "anymodel", key: "sk-any" })
  state = send(listing.state, { type: "models", reply: { ok: true, provider: "anymodel", models: ["a/one", "b/two", "b/three"] } }).state
  state = typed(state, "b/")
  assert.equal(send(state, key("down")).state.modelIndex, 1)
  const picked = send(send(state, key("down")).state, key("enter"))
  assert.deepEqual(picked.effect, { type: "connect", provider: "anymodel", key: "sk-any", model: "b/three" })
  // Nothing matches: what was typed is the id.
  const custom = typed({ ...state, filter: "", filterCursor: 0 }, "my/model")
  assert.deepEqual(send(custom, key("enter")).effect, { type: "connect", provider: "anymodel", key: "sk-any", model: "my/model" })
})

test("an OAuth provider connects without asking for a key", () => {
  const state = send(send(ready(), key("enter")).state, key("down")).state
  const chosen = send(state, key("enter"))
  assert.deepEqual(chosen.effect, { type: "connect", provider: "chatgpt", key: "", model: "" })
  const failed = send(chosen.state, { type: "connected", reply: { ok: false, error: "closed" } }).state
  assert.deepEqual([failed.step, failed.error], ["provider", "closed"])
})

test("More providers hands over to the classic terminal; Ctrl+C and Esc on the first screen quit", () => {
  let state = send(ready(), key("enter")).state
  state = send(state, key("up")).state // wraps to the last entry
  assert.deepEqual(send(state, key("enter")).effect, { type: "finish", outcome: { kind: "classic" } })
  assert.deepEqual(send(ready(), key("escape")).effect, { type: "finish", outcome: { kind: "quit" } })
  const interrupt = send(state, { type: "input", event: { type: "key", key: "char", text: "c", ctrl: true } })
  assert.deepEqual(interrupt.effect, { type: "finish", outcome: { kind: "quit" } })
  assert.equal(send(send(ready(), key("enter")).state, key("escape")).state.step, "welcome")
})

test("every frame is exactly as wide as the window and its hit boxes answer clicks", () => {
  for (const [cols, rows] of [[120, 36], [80, 24], [MIN_COLS, MIN_ROWS], [30, 10]] as const) {
    const scene = renderScene({ state: send(ready(), key("enter")).state, strings, cols, rows, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
    assert.equal(scene.rows.length, rows)
    for (const row of scene.rows) assert.equal([...plain(row)].length, cols)
  }
  const scene = renderScene({ state: send(ready(), key("enter")).state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  const options = scene.hits.filter((hit) => hit.target.kind === "option")
  assert.equal(options.length, providers.length + 1)
  const text = scene.rows.map(plain)
  assert.match(text[options[1]!.row]!, /chatgpt/)
})

test("without colour there is no picture, only the panel", () => {
  const scene = renderScene({ state: ready(), strings, cols: 80, rows: 24, level: "none", brightness: 0.4, panel: 1, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  assert.ok(scene.rows.every((row) => !/[▀▄▌▐▘▝▖▗▚▞▛▜▙▟█]/.test(row)))
  assert.ok(scene.rows.some((row) => plain(row).includes("Welcome to Kvist")))
})

test("the panel fades in: nothing at first, all of it at the end, and the picture rests in between", () => {
  const at = (panel: number) => renderScene({ state: ready(), strings, cols: 80, rows: 24, level: "truecolor", brightness: 0.55, panel, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.doesNotMatch(at(0), /Welcome to Kvist/)
  assert.match(at(0.5), /Welcome to Kvist/)
  assert.match(at(1), /Welcome to Kvist/)
})

test("the intro lets the picture rest for four seconds before the panel appears, then settles fast", () => {
  assert.equal(introFrame(0).panel, 0)
  assert.equal(introFrame(3000).brightness, 1)
  assert.equal(introFrame(3999).panel, 0)
  assert.ok(introFrame(4100).panel > 0 && introFrame(4100).panel < 1)
  const end = introFrame(4300)
  assert.deepEqual([end.panel, end.brightness, end.done], [1, DIMMED, true])
})

test("frames in motion are sent smaller", () => {
  const full = pictureSize(100, 30, { width: 10, height: 20 })
  const half = pictureSize(100, 30, { width: 10, height: 20 }, 0.5)
  assert.deepEqual([half.width, half.height], [full.width / 2, full.height / 2])
})

const toKey = (state: SetupState) => send(send(ready(), key("enter")).state, key("enter")).state

test("a pasted key loses quotes and a Bearer prefix; Tab shows it in clear", () => {
  let state = send(toKey(ready()), key("down")).state
  state = send(ready(), key("enter")).state
  state = send(state, key("down")).state
  state = send(state, key("down")).state
  state = send(state, key("down")).state // anthropic
  state = send(state, key("enter")).state
  assert.equal(state.provider?.id, "openai" === state.provider?.id ? "openai" : state.provider?.id)
  const pasted = send(state, { type: "input", event: { type: "paste", text: '"Bearer sk-abc"\n' } }).state
  assert.equal(pasted.key, "sk-abc")
  assert.equal(pasted.reveal, false)
  assert.equal(send(pasted, key("tab")).state.reveal, true)
})

test("Esc during a connection abandons it and its late answer changes nothing", () => {
  let state = send(ready(), key("enter")).state
  state = send(state, key("down")).state
  state = send(state, key("down")).state
  state = send(state, key("enter")).state
  state = typed(state, "sk-1")
  const connecting = send(state, key("enter"))
  assert.equal(connecting.state.step, "working")
  const cancelled = send(connecting.state, key("escape"))
  assert.deepEqual([cancelled.state.step, cancelled.effect], ["key", { type: "cancel" }])
  const late = send(cancelled.state, { type: "connected", reply: { ok: true, provider: "openai", models: {} } })
  assert.equal(late.state.step, "key")
  // Keys other than Esc are ignored while connecting.
  assert.equal(send(connecting.state, key("enter")).state.step, "working")
})

test("a failed connection says what happened and what to do; success is confirmed on the next screen", () => {
  const base = (): SetupState => ({ ...ready(), step: "working", returnStep: "key", provider: providers[2] as never, key: "sk-1" })
  const failed = send(base(), { type: "connected", reply: { ok: false, error: "401 unauthorized" } }).state
  const scene = (state: SetupState, rows = 40) =>
    renderScene({ state, strings, cols: 100, rows, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  const shown = scene(failed)
  assert.match(shown, /Could not connect\./)
  assert.match(shown, /401 unauthorized/)
  assert.match(shown, /Check the key and try again/)
  const done = send(base(), { type: "connected", reply: { ok: true, provider: "openai", models: {} } }).state
  assert.match(scene(done), /Connected to openai\./)
  // The preview of the highlighted interface appears in a tall window only.
  assert.match(scene(done, 40), /Ask Kvist, or \/ for commands/)
  assert.doesNotMatch(scene(done, 24), /Ask Kvist, or \/ for commands/)
})

test("the first screen names the working folder and a wrong key prefix is noticed", () => {
  const welcome = renderScene({ state: ready(), strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "~/work/kvist", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.match(welcome, /Working folder: ~\/work\/kvist/)
  const anthropic = { id: "anthropic", label: "Anthropic", auth_type: "api_key" as const, requires_manual_models: false, has_credentials: false }
  const state: SetupState = { ...ready(), step: "key", provider: anthropic, key: "sk-live-123", cursor: 11 }
  const shown = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.match(shown, /Anthropic keys start with sk-ant-/)
})

test("the home folder is written as a tilde", () => {
  assert.equal(shortPath("/home/me/work", "/home/me"), "~/work")
  assert.equal(shortPath("/home/meow", "/home/me"), "/home/meow")
})

test("a button looks different when pointed at and when held down", () => {
  const start = { kind: "button", id: "primary" } as const
  const draw = (hover: typeof start | null, pressed: typeof start | null) =>
    renderScene({ state: ready(), strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover, pressed, icons: "plain", ghost: null, picture: "cells" })
  const rest = draw(null, null)
  const over = draw(start, null)
  const held = draw(start, start)
  assert.notDeepEqual(over.rows, rest.rows)
  assert.notDeepEqual(held.rows, over.rows)
  assert.notDeepEqual(held.rows, rest.rows)
  // Only the button's row changes.
  const changed = rest.rows.flatMap((row, index) => (row === over.rows[index] ? [] : [index]))
  assert.equal(changed.length, 1)
  // The text is the same in all three.
  assert.deepEqual(held.rows.map(plain), rest.rows.map(plain))
})

test("an option row is tinted when pointed at, also while selected", () => {
  const state = send(ready(), key("enter")).state
  const row = { kind: "option", index: 2 } as const
  const draw = (target: typeof row | null) =>
    renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: target, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows
  assert.notDeepEqual(draw(row), draw(null))
  const selected = draw({ kind: "option", index: 0 })
  assert.notDeepEqual(selected, draw(null))
})

test("a provider-only setup has no folder or interface step and ends with the provider saved", () => {
  const base: SetupState = { ...initialState("/w", "/h", false, "provider"), catalog: "ready", providers }
  // F does nothing, and the welcome screen does not offer the folder.
  assert.equal(send(base, { type: "input", event: { type: "key", key: "char", text: "f", ctrl: false } }).state.step, "welcome")
  const shown = renderScene({ state: base, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.doesNotMatch(shown, /Working folder/)
  // Provider, key: two steps.
  let state = send(send(base, key("enter")).state, key("down")).state
  state = send(state, key("down")).state
  state = send(state, key("enter")).state // openai
  const scene = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.map(plain).join("\n")
  assert.match(scene, /Step 2 of 2/)
  const typedKey = typed(state, "sk-1")
  const connecting = send(typedKey, key("enter")).state
  const done = send(connecting, { type: "connected", reply: { ok: true, provider: "openai", models: {} } })
  assert.deepEqual(done.effect, { type: "finish", outcome: { kind: "done" } })
})

const drawSetup = (state: SetupState, cols = 100, rows = 30, text = strings) =>
  renderScene({ state, strings: text, cols, rows, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: state.workspace, hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })

test("the first screen says what Kvist does not do, in a window as small as the minimum", () => {
  for (const [name, text] of [["en", strings], ["ru", stringsFor("ru")]] as const) {
    for (const [cols, rows] of [[120, 36], [MIN_COLS, MIN_ROWS]] as const) {
      const shown = drawSetup(initialState("/work/app", "/home/me"), cols, rows, text).rows.map(plain).join("\n")
      assert.match(shown, /\.env/, `${name} ${cols}x${rows} names .env`)
      assert.match(shown, /\/revert/, `${name} ${cols}x${rows} names /revert`)
      assert.match(shown, name === "en" ? /Get started/ : /Начать/, `${name} ${cols}x${rows} keeps its buttons`)
    }
  }
})

test("a wide working folder is asked about once, and the safe answer is the first one", () => {
  const at = (workspace: string): SetupState => ({ ...initialState(workspace, "/home/me"), catalog: "ready", providers })
  // A project folder goes straight on.
  assert.equal(send(at("/home/me/work/app"), key("enter")).state.step, "provider")
  // Home stops at a question that names the folder and what Kvist could do in it.
  const asked = send(at("/home/me"), key("enter")).state
  assert.equal(asked.step, "broad")
  const shown = drawSetup(asked).rows.map(plain).join("\n")
  assert.match(shown, /That is a wide folder/)
  assert.match(shown, /This is your home folder/)
  assert.match(shown, /Working folder: \/home\/me/)
  // Enter opens the chooser; nothing is kept.
  const choosing = send(asked, key("enter"))
  assert.equal(choosing.state.step, "folder")
  assert.deepEqual(choosing.effect, { type: "list", path: "/home/me" })
  assert.equal(choosing.state.broadAccepted, "")
  // Esc goes back to the first screen.
  assert.equal(send(asked, key("escape")).state.step, "welcome")
  // "Use it anyway", by key or by the button, goes on and is not asked again for that folder.
  for (const answer of [send(asked, key("char", "u")), send(asked, { type: "click", target: { kind: "button", id: "anyway" } })]) {
    assert.equal(answer.state.step, "provider")
    assert.equal(answer.state.broadAccepted, "/home/me")
    assert.equal(send({ ...answer.state, step: "welcome" }, key("enter")).state.step, "provider")
  }
  // Another wide folder is a new question.
  const root = { ...at("/home/me"), broadAccepted: "/home/me", workspace: "/" }
  assert.equal(send(root, key("enter")).state.step, "broad")
  // The hit boxes carry the three buttons.
  const ids = drawSetup(asked).hits.flatMap((hit) => (hit.target.kind === "button" ? [hit.target.id] : []))
  assert.deepEqual(ids, ["folder", "anyway", "back"])
  // A provider-only setup has no folder to warn about.
  const only = { ...initialState("/home/me", "/home/me", false, "provider"), catalog: "ready" as const, providers }
  assert.equal(send(only, key("enter")).state.step, "provider")
})

// ---- the installer's steps: what to install, then the setup carries on ----

const plan = {
  ok: true as const,
  os: "Linux",
  arch: "x86_64",
  shell: "bash",
  components: [
    { id: "cli", available: true, targets: ["/h/.local/share/kvist/kvist-core", "/h/.local/bin/kvist"] },
    { id: "node", available: true, targets: ["https://nodejs.org/dist/v22.23.3/node-v22.23.3-linux-x64.tar.gz", "/h/.local/share/kvist/node"] },
    { id: "jetbrains", available: false, targets: [] },
    { id: "vscode", available: true, targets: ["/usr/bin/code --install-extension kvist-vscode.vsix --force"] },
    { id: "path", available: true, targets: ["/h/.bashrc: export PATH=\"/h/.local/bin:$PATH\""] },
  ],
}
const installing = (): SetupState => send(initialState("/w", "/h", false, "full", true), { type: "plan", reply: plan }).state
const ticked = (state: SetupState) => state.install!.components.filter((component) => component.checked).map((component) => component.id)
const space = (): Action => key("char", " ")

test("install: the screen opens on the choice, with everything that exists ticked and what is missing left out", () => {
  const loading = initialState("/w", "/h", false, "full", true)
  assert.equal(loading.step, "components")
  assert.equal(loading.install?.plan, "loading")
  const state = installing()
  assert.deepEqual(state.install!.components.map((component) => component.id), ["cli", "node", "jetbrains", "vscode", "path"])
  assert.deepEqual(ticked(state), ["cli", "node", "vscode", "path"])
})

test("install: keyboard only — arrows move, space ticks, Enter installs just what is ticked", () => {
  let state = installing()
  state = send(state, key("down")).state
  state = send(state, key("down")).state
  state = send(state, key("down")).state
  assert.equal(state.install!.cursor, 3)
  state = send(state, space()).state
  assert.deepEqual(ticked(state), ["cli", "node", "path"])
  const run = send(state, key("enter"))
  assert.equal(run.state.step, "installing")
  assert.deepEqual(run.effect, { type: "install", only: ["cli", "node", "path"] })
})

test("install: mouse only — a click on a row ticks it, a click on Install starts, Quit leaves", () => {
  let state = installing()
  state = send(state, { type: "click", target: { kind: "option", index: 4 } }).state
  assert.deepEqual(ticked(state), ["cli", "node", "vscode"])
  assert.equal(state.install!.cursor, 4)
  const run = send(state, { type: "click", target: { kind: "button", id: "primary" } })
  assert.deepEqual(run.effect, { type: "install", only: ["cli", "node", "vscode"] })
  const quit = send(installing(), { type: "click", target: { kind: "button", id: "quit" } })
  assert.deepEqual(quit.effect, { type: "finish", outcome: { kind: "quit" } })
})

test("install: the program cannot be unticked and a component that is not here cannot be ticked", () => {
  let state = installing()
  state = send(state, space()).state
  assert.ok(ticked(state).includes("cli"))
  state = send(state, { type: "click", target: { kind: "option", index: 2 } }).state
  assert.ok(!ticked(state).includes("jetbrains"))
  // A digit key is a shortcut for the row: 5 is the PATH line.
  state = send(state, key("char", "5")).state
  assert.ok(!ticked(state).includes("path"))
})

test("install: progress events fill the list, and a finished run goes on to the welcome screen", () => {
  let state = send(installing(), key("enter")).state
  state = send(state, { type: "installEvent", event: { event: "component", id: "cli", status: "start" } }).state
  state = send(state, { type: "installEvent", event: { event: "step", message: "Downloading kvist-linux-x64" } }).state
  assert.equal(state.install!.status.cli, "running")
  assert.equal(state.install!.message, "Downloading kvist-linux-x64")
  state = send(state, { type: "installEvent", event: { event: "component", id: "cli", status: "done" } }).state
  const warning = send(state, { type: "installEvent", event: { event: "warning", message: "could not install the extension into code" } }).state
  assert.deepEqual(warning.install!.warnings, ["could not install the extension into code"])
  const welcome = send(state, { type: "installed", reply: { ok: true } }).state
  assert.equal(welcome.step, "welcome")
  const scene = renderScene({ state: welcome, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  assert.match(plain(scene.rows.join("\n")), /Installed\./)
})

test("install: a failed run returns to the choice with the reason, and Ctrl+C leaves from any step", () => {
  const running = send(installing(), key("enter")).state
  const failed = send(running, { type: "installed", reply: { ok: false, error: "could not download kvist-linux-x64" } }).state
  assert.equal(failed.step, "components")
  assert.equal(failed.install!.error, "could not download kvist-linux-x64")
  assert.deepEqual(ticked(failed), ["cli", "node", "vscode", "path"])
  const ctrlC = { type: "input", event: { type: "key", key: "char", text: "c", ctrl: true } } as Action
  assert.deepEqual(send(running, ctrlC).effect, { type: "finish", outcome: { kind: "quit" } })
  // Keys during the run do nothing but Ctrl+C.
  assert.equal(send(running, key("enter")).state.step, "installing")
})

test("install: a plan that cannot be read says so and offers only Quit", () => {
  const state = send(initialState("/w", "/h", false, "full", true), { type: "plan", reply: { ok: false, error: "no bash" } }).state
  assert.equal(state.install!.plan, "failed")
  assert.equal(send(state, key("enter")).effect, undefined)
  const scene = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  assert.match(plain(scene.rows.join("\n")), /Could not read the installation plan\. no bash/)
})

test("install: the choice shows the highlighted component's paths and commands and its rows are click targets", () => {
  let state = installing()
  for (let step = 0; step < 3; step++) state = send(state, key("down")).state
  const scene = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  const text = plain(scene.rows.join("\n"))
  assert.match(text, /\[x\] {2}Kvist agent/)
  assert.match(text, /\[ \] {2}JetBrains plugin.*not found here/)
  assert.match(text, /Runs the editor's own installer:/)
  assert.match(text, /\/usr\/bin\/code --install-extension kvist-vscode\.vsix --force/)
  const rows = scene.hits.filter((hit) => hit.target.kind === "option").map((hit) => (hit.target.kind === "option" ? hit.target.index : -1))
  assert.deepEqual(rows, [0, 1, 2, 3, 4])
  assert.ok(scene.hits.some((hit) => hit.target.kind === "button" && hit.target.id === "primary"))
  assert.match(plain(scene.rows.join("\n")), /Space tick/)
})

test("install: the progress screen lists the chosen components with their state and a bar", () => {
  let state = send(installing(), key("enter")).state
  state = send(state, { type: "installEvent", event: { event: "component", id: "cli", status: "done" } }).state
  state = send(state, { type: "installEvent", event: { event: "component", id: "vscode", status: "start" } }).state
  const scene = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.4, panel: 1, tick: 3, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  const text = plain(scene.rows.join("\n"))
  assert.match(text, /✓ {2}Kvist agent\s+done/)
  assert.match(text, /VS Code extension\s+working/)
  assert.match(text, /Add to PATH\s+waiting/)
  assert.match(text, /1\/4/)
})

test("install: the Russian copy has the same keys as the English one", () => {
  const ru = stringsFor("ru")
  assert.deepEqual(Object.keys(ru.componentLabels).sort(), Object.keys(strings.componentLabels).sort())
  assert.deepEqual(Object.keys(ru.componentActions).sort(), Object.keys(strings.componentActions).sort())
})

test("install: a long description of what will be done wraps inside the panel", () => {
  const state = send(installing(), key("down")).state
  const scene = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  const rows = scene.rows.map(plain)
  assert.ok(rows.some((row) => row.includes("Downloads a pinned Node.js")))
  assert.ok(!rows.some((row) => row.includes("unpacks it into:") && row.includes("Downloads a pinned Node.js from nodejs.org, checks")))
  const panel = scene.panel!
  const [first] = rows.filter((row) => row.includes("Downloads a pinned"))
  assert.ok(first!.indexOf("Downloads") + "Downloads a pinned Node.js from nodejs.org, checks its".length < panel.col + panel.width)
})

// ---- the folding row of build tools (system packages, for building from source) ----

const withBuildTools = (privilege: "root" | "cached" | "password" | "none") => ({
  ...plan,
  privilege,
  components: [
    ...plan.components,
    { id: "python", available: true, reason: "", targets: ["sudo apt-get update", "sudo apt-get install -y python3 python3-venv python3-pip"] },
    { id: "libpython", available: true, reason: "", targets: ["sudo apt-get update", "sudo apt-get install -y python3-dev"] },
    { id: "binutils", available: false, reason: "present", targets: [] },
  ],
})
const withTools = (privilege: "root" | "cached" | "password" | "none" = "cached"): SetupState =>
  send(initialState("/w", "/h", false, "full", true), { type: "plan", reply: withBuildTools(privilege) }).state
const sceneOf = (state: SetupState, rows = 34) =>
  plain(renderScene({ state, strings, cols: 110, rows, level: "truecolor", brightness: 0.4, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows.join("\n"))
const groupIndex = (state: SetupState) => choiceRows(state.install!).findIndex((row) => row.kind === "group")

test("build tools: folded but ticked by default, and the folding row says how many", () => {
  const state = withTools()
  assert.equal(state.install!.buildOpen, false)
  assert.deepEqual(chosenComponents(state.install!), ["cli", "node", "vscode", "path", "python", "libpython"])
  assert.match(sceneOf(state), /▸\s+Developer tools\s+2 ticked/)
  const rows = choiceRows(state.install!)
  assert.equal(rows.filter((row) => row.kind === "component").length, 4 + 1)
  assert.equal(rows.at(-1)?.kind, "group")
  assert.match(sceneOf(state), /▸\s+Developer tools/)
})

test("build tools: a click on the folding row unfolds and folds it, and a click on an item ticks it", () => {
  let state = withTools()
  const header = groupIndex(state)
  state = send(state, { type: "click", target: { kind: "option", index: header } }).state
  assert.equal(state.install!.buildOpen, true)
  assert.match(sceneOf(state), /▾\s+Developer tools/)
  assert.match(sceneOf(state), /\[ \] {2}binutils \(objdump\)\s+already installed/)
  assert.match(sceneOf(state), /\[x\] {2}Python 3, venv, pip/)
  // The item right under the folding row is Python: a click unticks it.
  state = send(state, { type: "click", target: { kind: "option", index: header + 1 } }).state
  assert.ok(!chosenComponents(state.install!).includes("python"))
  state = send(state, { type: "click", target: { kind: "option", index: header } }).state
  assert.equal(state.install!.buildOpen, false)
  // A folded group still reports what is ticked inside it.
  assert.match(sceneOf(state), /▸\s+Developer tools\s+1 ticked/)
})

test("build tools: → unfolds and ← folds from the row or from inside, Space on the row does both", () => {
  let state = withTools()
  const header = groupIndex(state)
  for (let step = 0; step < header; step++) state = send(state, key("down")).state
  assert.equal(state.install!.cursor, header)
  state = send(state, key("right")).state
  assert.equal(state.install!.buildOpen, true)
  state = send(state, key("down")).state
  assert.equal(state.install!.cursor, header + 1)
  // ← from inside folds the group and the highlight goes back to its row.
  state = send(state, key("left")).state
  assert.equal(state.install!.buildOpen, false)
  assert.equal(state.install!.cursor, header)
  state = send(state, space()).state
  assert.equal(state.install!.buildOpen, true)
  state = send(state, space()).state
  assert.equal(state.install!.buildOpen, false)
  // On an ordinary row the arrows do nothing to it.
  state = send({ ...state, install: { ...state.install!, cursor: 0 } }, key("right")).state
  assert.equal(state.install!.buildOpen, false)
})

test("build tools: an item that is already there or not offered cannot be ticked", () => {
  let state = withTools()
  state = send(state, { type: "click", target: { kind: "option", index: groupIndex(state) } }).state
  const binutils = groupIndex(state) + 3
  state = send(state, { type: "click", target: { kind: "option", index: binutils } }).state
  assert.ok(!chosenComponents(state.install!).includes("binutils"))
})

test("build tools: a sudo that needs a password hands the terminal over first, then installs", () => {
  const state = withTools("password")
  // The build tools are ticked from the start, so the password is asked for at once.
  const start = send(state, key("enter"))
  assert.deepEqual(start.effect, { type: "elevate" })
  assert.equal(start.state.step, "installing")
  const granted = send(start.state, { type: "elevated", error: "" })
  assert.deepEqual(granted.effect, { type: "install", only: ["cli", "node", "vscode", "path", "python", "libpython"] })
  const refused = send(start.state, { type: "elevated", error: "Administrator rights were not granted, nothing was installed." })
  assert.equal(refused.state.step, "components")
  assert.match(refused.state.install!.error, /not granted/)
})

test("build tools: nothing is asked when sudo is cached or the person is root, and the plain run needs none", () => {
  for (const privilege of ["root", "cached", "none"] as const) {
    assert.equal(send(withTools(privilege), key("enter")).effect?.type, "install", privilege)
  }
  // With every build tool unticked a password sudo is never asked for.
  let state = withTools("password")
  const header = groupIndex(state)
  state = send(state, { type: "click", target: { kind: "option", index: header } }).state
  state = send(state, { type: "click", target: { kind: "option", index: header + 1 } }).state
  state = send(state, { type: "click", target: { kind: "option", index: header + 2 } }).state
  assert.equal(send(state, key("enter")).effect?.type, "install")
})

test("build tools: the highlighted item shows the commands and what will be asked", () => {
  let state = withTools("password")
  const header = groupIndex(state)
  state = send(state, { type: "click", target: { kind: "option", index: header } }).state
  const folded = sceneOf(send(state, { type: "click", target: { kind: "option", index: header } }).state)
  assert.match(folded, /Python, Rust and the packages for building Kvist yourself/)
  state = send(state, key("down")).state
  const text = sceneOf(state)
  assert.match(text, /Runs the package manager \(administrator rights\):/)
  assert.match(text, /sudo apt-get install -y python3 python3-venv python3-pip/)
  assert.match(text, /Your password will be asked for first/)
  const noSudo = sceneOf(send(withTools("none"), { type: "click", target: { kind: "option", index: header } }).state)
  assert.ok(noSudo.includes("Developer tools"))
})

test("build tools: a command that could not run is marked failed and its advice survives to the welcome screen", () => {
  let state = withTools("cached")
  const header = groupIndex(state)
  state = send(state, { type: "click", target: { kind: "option", index: header } }).state
  state = send(state, { type: "click", target: { kind: "option", index: header + 1 } }).state
  state = send(state, key("enter")).state
  state = send(state, { type: "installEvent", event: { event: "component", id: "python", status: "start" } }).state
  state = send(state, { type: "installEvent", event: { event: "warning", message: "could not run it here; run it yourself: sudo apt-get install -y python3" } }).state
  state = send(state, { type: "installEvent", event: { event: "component", id: "python", status: "failed" } }).state
  assert.equal(state.install!.status.python, "failed")
  assert.match(sceneOf(state), /✗\s+Python 3, venv, pip\s+failed/)
  state = send(state, { type: "installed", reply: { ok: true } }).state
  assert.equal(state.step, "welcome")
  assert.match(sceneOf(state), /run it yourself: sudo apt-get install/)
})

test("build tools: the Russian copy has every key the English one has", () => {
  const ru = stringsFor("ru")
  for (const id of ["python", "libpython", "binutils"]) {
    assert.ok(ru.componentLabels[id] && ru.componentActions[id], id)
  }
  assert.deepEqual(Object.keys(ru.componentReason).sort(), Object.keys(strings.componentReason).sort())
  assert.ok(ru.buildGroup[0] && ru.elevateTitle && ru.privilegePassword)
})

// ---- no colour: selection has to survive NO_COLOR, TERM=dumb and TERM=linux ----

interface Span {
  row: number
  text: string
}

// The runs of cells drawn in inverse video, and the rows underlined, read back from the ANSI rows.
function inverseSpans(rows: string[]): Span[] {
  const spans: Span[] = []
  rows.forEach((raw, row) => {
    let inverse = false
    let run = ""
    const flush = () => {
      if (run) spans.push({ row, text: run })
      run = ""
    }
    for (const part of raw.split(/(\x1b\[[0-9;]*m)/)) {
      const codes = /^\x1b\[([0-9;]*)m$/.exec(part)
      if (!codes) {
        if (inverse) run += part
        continue
      }
      const params = (codes[1] || "0").split(";")
      for (let at = 0; at < params.length; at++) {
        const code = params[at]
        // Colour parameters carry their own numbers: 38;5;n and 38;2;r;g;b.
        if (code === "38" || code === "48") at += params[at + 1] === "2" ? 4 : 2
        else if (code === "0") {
          flush()
          inverse = false
        } else if (code === "7") inverse = true
      }
    }
    flush()
  })
  return spans
}

const mono = (state: SetupState, extra: { hover?: Parameters<typeof renderScene>[0]["hover"]; pressed?: Parameters<typeof renderScene>[0]["pressed"]; cols?: number; rows?: number } = {}) =>
  renderScene({ state, strings, cols: extra.cols ?? 100, rows: extra.rows ?? 30, level: "none", brightness: 0.55, panel: 1, tick: 0, workspace: state.workspace || "/w", hover: extra.hover ?? null, pressed: extra.pressed ?? null, icons: "plain", ghost: null, picture: "cells" }).rows
// The text the panel shows in inverse video, with the panel's frame removed.
const inverseTexts = (rows: string[]) => inverseSpans(rows).map((span) => span.text.trim())

test("without colour the button Enter presses is in inverse video and no other button is", () => {
  const welcome = mono(initialState("/w", "/h"))
  assert.deepEqual(inverseTexts(welcome), ["Get started"])
  assert.ok(welcome.map(plain).some((row) => row.includes("Working folder") && row.includes("Quit")))
  // The same screen with colour draws no inverse video: colour levels keep their own look.
  const coloured = renderScene({ state: initialState("/w", "/h"), strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "/w", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" }).rows
  assert.deepEqual(inverseSpans(coloured), [])
})

test("without colour the highlighted provider is a marked inverse row and follows the arrows", () => {
  let state = send(ready(), key("enter")).state
  for (const expected of ["anymodel", "chatgpt", "openai"]) {
    const rows = mono(state)
    const marked = rows.map(plain).filter((row) => row.includes("│ >"))
    assert.equal(marked.length, 1, "exactly one marked row")
    assert.match(marked[0]!, new RegExp(`>\\d\\s+${expected}`))
    const inverse = inverseSpans(rows).filter((span) => span.text.includes(expected))
    assert.equal(inverse.length, 1, "the marked row is inverse")
    state = send(state, key("down")).state
  }
})

test("without colour the highlighted row is marked on the model, interface and installer steps", () => {
  const marked = (state: SetupState) => mono(state, { rows: 34 }).map(plain).filter((row) => row.includes("│ >"))
  let models = send(send(ready(), key("enter")).state, key("enter")).state
  models = send(send(typed(models, "sk-any"), key("enter")).state, { type: "models", reply: { ok: true, provider: "anymodel", models: ["a/one", "b/two"] } }).state
  models = send(models, key("down")).state
  assert.equal(marked(models).length, 1)
  assert.match(marked(models)[0]!, />\s*b\/two/)

  const connected = send(send(send(ready(), key("enter")).state, key("down")).state, key("down")).state
  const openai = send(connected, key("enter")).state
  const toInterface = send(send(typed(openai, "sk-1"), key("enter")).state, { type: "connected", reply: { ok: true, provider: "openai", models: {} } }).state
  assert.equal(toInterface.step, "interface")
  assert.equal(marked(toInterface).length, 1)
  assert.match(marked(toInterface)[0]!, />1\s+Claude/)
  assert.match(marked(send(toInterface, key("down")).state)[0]!, />2\s+/)
  // The button that finishes the step is inverse too, as Enter would press it.
  assert.ok(inverseTexts(mono(toInterface, { rows: 34 })).includes(strings.ready))

  const install = installing()
  assert.equal(marked(install).length, 1)
  assert.match(marked(install)[0]!, />\s*\[x\]\s+Kvist agent/)
  assert.match(marked(send(install, key("down")).state)[0]!, />\s*\[x\]\s+Node/)
  assert.ok(inverseTexts(mono(install, { rows: 34 })).includes(strings.install))
})

test("without colour the wide-folder warning marks the safe answer", () => {
  const asked = send({ ...initialState("/home/me", "/home/me"), catalog: "ready" as const, providers }, key("enter")).state
  assert.equal(asked.step, "broad")
  assert.deepEqual(inverseTexts(mono(asked)).map((text) => text.replace(/^\S+\s/, "")), [strings.broadChoose])
})

test("without colour the folder chooser marks the highlighted row, and the focused button", () => {
  const start: SetupState = { ...initialState("/home/me/work", "/home/me"), catalog: "ready" }
  const opened = send(start, key("char", "f")).state
  const listed = send(opened, { type: "listing", listing: { path: "/home/me/work", dirs: [{ name: "app", git: true }, { name: "docs", git: false }], files: 1 } }).state
  const marked = (state: SetupState) => mono(state, { rows: 30 }).map(plain).filter((row) => row.includes("│ >"))
  assert.equal(marked(listed).length, 1)
  assert.match(marked(listed)[0]!, new RegExp(strings.folderUse))
  const down = send(listed, key("down")).state
  assert.match(marked(down)[0]!, /\.\./)
  // The default button is inverse while the list has focus.
  assert.ok(inverseTexts(mono(listed)).some((text) => text.endsWith(strings.folderUseButton)))
  // With Cancel focused the list marks nothing and Cancel is the inverse button.
  const onCancel = { ...listed, folder: { ...listed.folder!, focus: "cancel" as const } }
  assert.equal(marked(onCancel).length, 0)
  const inverse = inverseTexts(mono(onCancel))
  assert.ok(inverse.some((text) => text.endsWith(strings.cancel)))
  assert.ok(!inverse.some((text) => text.endsWith(strings.folderUseButton)))
})

test("without colour the pointer is underlined and a held button changes again", () => {
  const welcome = initialState("/w", "/h")
  const quit = { kind: "button", id: "quit" } as const
  const underlined = (rows: string[]) => rows.map((row, index) => (/\x1b\[(?:[0-9;]*;)?4[;m]/.test(row) ? index : -1)).filter((index) => index >= 0)
  assert.equal(underlined(mono(welcome)).length, 0)
  assert.equal(underlined(mono(welcome, { hover: quit })).length, 1)
  assert.notDeepEqual(mono(welcome, { hover: quit, pressed: quit }), mono(welcome, { hover: quit }))
  // Pointing at a row that is not highlighted underlines it; the highlighted row stays marked.
  const provider = send(ready(), key("enter")).state
  const over = mono(provider, { hover: { kind: "option", index: 2 } })
  assert.equal(over.map(plain).filter((row) => row.includes("│ >")).length, 1)
  assert.equal(underlined(over).length, 1)
})
