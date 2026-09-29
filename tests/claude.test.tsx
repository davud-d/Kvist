import assert from "node:assert/strict"
import { after, before, test } from "node:test"

import { render } from "ink-testing-library"
import { Text } from "ink"
import { createElement, type ReactElement } from "react"

import type { Item, State } from "../state.js"
import { initialState } from "../state.js"
import { ClaudeEffort, ClaudeFooter, ClaudeItemView, ClaudeLiveBody, ClaudeScreen, ClaudeSpinner, collapse, exploreLine } from "../ui/claude.js"
import { TIPS, TIPS_RU, tipFor } from "../ui/tips.js"
import { ViewContext } from "../ui/context.js"
import { InputBox } from "../ui/input.js"
import { LOGO_WIDTH, logoRows } from "../ui/logo.js"
import { VERBS, VERBS_RU, doneLine, verbAt } from "../ui/verbs.js"
import { applySkin } from "../ui/theme.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const ok = { ok: true, summary: "Read 120 lines", output: "", exit_code: null, path: null, first_line: null }
const view = { workspace: "/work/project", info: { provider: "anymodel", model: "sonnet", role: "coder", session: "s", kvistVersion: "1.2.3" }, effort: "high", pointer: null, expanded: false }

before(() => applySkin("claude"))
after(() => applySkin("ink"))

function frame(element: ReactElement): string {
  const app = render(createElement(ViewContext.Provider, { value: view }, element))
  const out = plain(app.lastFrame())
  app.unmount()
  return out
}

function item(fields: Partial<Item> & { kind: Item["kind"] }): Item {
  return { id: 1, ...fields } as Item
}

const tool = (action: string, args: Record<string, unknown>, result: Partial<typeof ok> = {}) =>
  item({ kind: "tool", step: 1, planStep: "inspect", action, args, result: { ...ok, ...result }, batchIndex: 0 })

test("the compact logo is three rows of half blocks, the large one six rows of double-width blocks", () => {
  const compact = logoRows("compact")
  assert.equal(compact.length, 3)
  for (const row of compact) assert.equal([...row.shade + row.main].length, LOGO_WIDTH.compact)
  const large = logoRows("large")
  assert.equal(large.length, 6)
  for (const row of large) assert.equal([...row.shade + row.main].length, LOGO_WIDTH.large)
  assert.match(large[0]?.shade ?? "", /██/)
})

test("the banner names Kvist with its version, the model and effort, the provider and the workspace", () => {
  const out = frame(createElement(ClaudeItemView, { item: item({ kind: "banner" }), showDebug: false }))
  assert.match(out, /Kvist v1\.2\.3/)
  assert.match(out, /sonnet with high effort · anymodel/)
  assert.match(out, /\/work\/project/)
  assert.doesNotMatch(out, /Claude/)
})

test("a request is a ❯ row and an answer is a ● paragraph", () => {
  assert.match(frame(createElement(ClaudeItemView, { item: item({ kind: "user", text: "fix it" }), showDebug: false })), /❯ fix it/)
  assert.match(frame(createElement(ClaudeItemView, { item: item({ kind: "answer", text: "All **done**", done: true }), showDebug: false })), /● All done/)
})

test("a final_answer call prints no line of its own, and a code block has no frame", () => {
  const step = item({ kind: "step", step: 1, planStep: "done", action: "final_answer", target: "", note: "", batchIndex: 0 })
  assert.equal(frame(createElement(ClaudeItemView, { item: step, showDebug: false })).trim(), "")
  const answer = item({ kind: "answer", text: "Run:\n\n```sh\nmake test\n```", done: true })
  const out = frame(createElement(ClaudeItemView, { item: answer, showDebug: false }))
  assert.match(out, /make test/)
  assert.doesNotMatch(out, /[╭╰│]|\bsh\b/)
})

test("a proposed plan is a call with its Markdown under ⎿, labelled in the request's language", () => {
  const markdown = "**Goal**\n\n- split `agent.py`\n- keep the tests green"
  const english = frame(createElement(ClaudeItemView, { item: item({ kind: "plan", markdown, russian: false }), showDebug: false }))
  assert.match(english, /● Proposed plan/)
  assert.match(english, /⎿ {2}Goal/)
  assert.match(english, /split agent\.py/)
  const russian = frame(createElement(ClaudeItemView, { item: item({ kind: "plan", markdown, russian: true }), showDebug: false }))
  assert.match(russian, /● Предложенный план/)
})

test("a read is a Name(target) call with its summary under ⎿", () => {
  const out = frame(createElement(ClaudeItemView, { item: tool("read_file", { path: "a.py" }), showDebug: false }))
  assert.match(out, /● Read\(a\.py\)/)
  assert.match(out, /⎿ {2}Read 120 lines/)
})

test("an edit counts additions and removals and numbers the diff", () => {
  const edit = tool("replace_in_file", { path: "a.py", old_text: "a\nb\n", new_text: "a\nc\nd\n" }, { path: "a.py", first_line: 4 })
  const out = frame(createElement(ClaudeItemView, { item: edit, showDebug: false }))
  assert.match(out, /● Update\(a\.py\)/)
  assert.match(out, /Added 2 lines, removed 1 line/)
  assert.match(out, /5 − b/)
  assert.match(out, /5 \+ c/)
})

test("a failing command shows its exit code, output is cut to ten lines", () => {
  const failed = tool("run_shell", { command: ["make"] }, { ok: false, exit_code: 2, output: "boom", summary: "failed" })
  const out = frame(createElement(ClaudeItemView, { item: failed, showDebug: false }))
  assert.match(out, /● Bash\(make\)/)
  assert.match(out, /Error: Exit code 2/)
  assert.match(out, /boom/)

  const long = tool("run_shell", { command: ["seq", "30"] }, { exit_code: 0, output: Array.from({ length: 30 }, (_, i) => String(i + 1)).join("\n") })
  const cut = frame(createElement(ClaudeItemView, { item: long, showDebug: false }))
  assert.match(cut, /… \+20 lines/)
  assert.doesNotMatch(cut, /\n\s+11\n/)
})

const shell = (id: number, fields: Partial<typeof ok> = {}): Item => ({ id, kind: "tool", step: id, planStep: "verify", action: "run_shell", args: { command: ["ls"] }, result: { ...ok, exit_code: 0, ...fields }, batchIndex: 0 })
const explored = (id: number): Item => ({ id, kind: "explored", counts: { reads: 3, searches: 2, lists: 0, git: 0 }, names: [] })

test("successful shell commands and the reads around them fold into one dim line", () => {
  const entries = collapse([explored(1), shell(2), shell(3), { id: 4, kind: "say", text: "next" }, shell(5)])
  assert.equal(entries.length, 3)
  const [group, say, last] = entries
  assert.ok(group && "counts" in group && group.shells === 2 && group.counts.reads === 3)
  assert.ok(say && "item" in say && say.item.kind === "say")
  assert.ok(last && "counts" in last && last.shells === 1)
  assert.equal(exploreLine({ reads: 3, searches: 2, lists: 0, git: 0 }, 2), "Searched for 2 patterns, read 3 files, ran 2 shell commands")
  assert.equal(exploreLine({ reads: 0, searches: 0, lists: 0, git: 0 }, 1), "Ran 1 shell command")
})

test("reads alone keep their own line, and a failed command is never folded away", () => {
  assert.deepEqual(collapse([explored(1)]).map((entry) => "item" in entry), [true])
  const failed = shell(3, { ok: false, exit_code: 1, output: "boom" })
  const entries = collapse([shell(1), failed, shell(4)])
  assert.deepEqual(entries.map((entry) => ("item" in entry ? "item" : "group")), ["group", "item", "group"])
})

test("a folded run of commands is one line under Ctrl+O's opposite, and the full commands under Ctrl+O", async () => {
  const items = [shell(1), shell(2)]
  const show = async (expanded: boolean) => {
    const app = render(
      createElement(
        ViewContext.Provider,
        { value: { ...view, expanded } },
        createElement(ClaudeScreen, { items, showDebug: false, followKey: 0, live: null, spinner: null }, createElement(Text, null, "INPUT")),
      ),
    )
    await tick()
    await tick()
    const out = plain(app.lastFrame())
    app.unmount()
    return out
  }
  const folded = await show(false)
  assert.match(folded, /^\s*Ran 2 shell commands/m)
  assert.doesNotMatch(folded, /Bash\(ls\)/)
  const open = await show(true)
  assert.equal(open.match(/Bash\(ls\)/g)?.length, 2)
})

test("explored reads are summarised the way Claude Code words them", () => {
  assert.equal(exploreLine({ reads: 3, searches: 2, lists: 0, git: 0 }), "Searched for 2 patterns, read 3 files")
  assert.equal(exploreLine({ reads: 1, searches: 0, lists: 1, git: 0 }), "Read 1 file, listed 1 directory")
})

test("a command's reply hangs off ⎿", () => {
  const out = frame(createElement(ClaudeItemView, { item: item({ kind: "note", tone: "info", text: "Set model to fast" }), showDebug: false }))
  assert.match(out, /⎿ {2}Set model to fast/)
})

test("a long reply wraps inside the terminal width and keeps its glyph gap", () => {
  const words = Array.from({ length: 40 }, (_, i) => `word${i}`).join(" ")
  for (const kind of ["note", "answer"] as const) {
    const out = frame(createElement(ClaudeItemView, { item: item({ kind, tone: "info", text: words, done: true } as Partial<Item> & { kind: Item["kind"] }), showDebug: false }))
    const rows = out.split("\n").filter((row) => row.trim())
    assert.ok(rows.length > 1)
    for (const row of rows) assert.ok([...row].length <= 100, `row is ${[...row].length} cells: ${row}`)
    assert.match(rows[0] ?? "", kind === "note" ? /^ {2}⎿ {2}word0 / : /^● word0 /)
    assert.match(rows[1] ?? "", kind === "note" ? /^ {5}\S/ : /^ {2}\S/)
  }
})

const tick = () => new Promise((resolve) => setTimeout(resolve, 50))

async function screen(items: Item[]): Promise<string[]> {
  const app = render(
    createElement(ViewContext.Provider, { value: view }, createElement(ClaudeScreen, { items, showDebug: false, followKey: 0, live: null, spinner: null }, createElement(Text, null, "INPUT"))),
  )
  await tick()
  await tick()
  const rows = plain(app.lastFrame()).split("\n")
  app.unmount()
  return rows
}

test("the welcome block is the top of the screen and the input stays at the bottom", async () => {
  const rows = await screen([item({ kind: "banner" })])
  assert.match(rows.slice(0, 8).join("\n"), /Kvist v1\.2\.3/)
  assert.equal(rows.at(-1), "INPUT")
  assert.ok(rows.length >= 20, `the frame fills the window, got ${rows.length} rows`)
})

test("a long transcript shows its newest rows above the input", async () => {
  const items = Array.from({ length: 80 }, (_, i) => item({ id: i + 1, kind: "note", tone: "info", text: `row ${i}` }))
  const rows = await screen(items)
  assert.equal(rows.at(-1), "INPUT")
  assert.match(rows.at(-2) ?? "", /row 79/)
  assert.doesNotMatch(rows.join("\n"), /row 0\b/)
})

test("the input sits between two rules and its footer says what the keys do", () => {
  const caption = createElement(ClaudeFooter, { running: false, armed: false, usage: { input: 0, output: 0, context: 12_300, limit: 200_000 }, model: "sonnet", role: "coder" })
  const out = frame(createElement(InputBox, { active: true, history: [], placeholder: "Ask Kvist", onSubmit: () => {}, caption }))
  const lines = out.split("\n")
  assert.match(lines[0] ?? "", /^─+$/)
  assert.match(lines[1] ?? "", /^ ❯ /)
  assert.match(lines[2] ?? "", /^─+$/)
  assert.match(out, /\/ for commands\s+12\.3K \(6%\)\s+sonnet · coder/)
})

test("the footer asks for a second esc while a stop is armed", () => {
  const usage = { input: 0, output: 0, context: 0, limit: 0 }
  assert.match(frame(createElement(ClaudeFooter, { running: true, armed: false, usage, model: "m", role: "r" })), /esc to interrupt/)
  assert.match(frame(createElement(ClaudeFooter, { running: true, armed: true, usage, model: "m", role: "r" })), /esc again to interrupt/)
})

const agent = (role = "coder"): State => {
  const base = initialState()
  return { ...base, info: { ...base.info, role } }
}

test("a first Ctrl+C is answered with a warning where the model and context usually are", () => {
  const usage = { input: 0, output: 0, context: 12_300, limit: 200_000 }
  const idle = frame(createElement(ClaudeFooter, { running: false, armed: false, usage, model: "sonnet", role: "coder", }))
  assert.match(idle, /\/ for commands\s+12\.3K/)
  const warned = frame(createElement(ClaudeFooter, { running: false, armed: false, usage, model: "sonnet", role: "coder", exitWarning: "C" as const }))
  assert.match(warned, /\/ for commands\s+Press Ctrl\+C again to exit and stop the agent/)
  assert.doesNotMatch(warned, /sonnet/)
})

test("the warning names the key that was pressed, Ctrl+D as well as Ctrl+C", () => {
  const usage = { input: 0, output: 0, context: 0, limit: 0 }
  const warned = frame(createElement(ClaudeFooter, { running: false, armed: false, usage, model: "m", role: "r", exitWarning: "D" as const }))
  assert.match(warned, /Press Ctrl\+D again to exit and stop the agent/)
})

test("a running turn shows a verb, the elapsed time, what the core reports, and the stop hint", () => {
  const startedAt = Date.now() - 5000
  const state: State = { ...agent(), running: true, startedAt, status: { text: "Reading files", step: 1, planStep: "inspect", error: false } }
  const out = frame(createElement(ClaudeSpinner, { state }))
  const verb = VERBS.find((candidate) => out.includes(`${candidate}…`))
  assert.ok(verb, `no verb of the list in: ${out}`)
  assert.match(out, /[·✢✳✶✻✽] .+… \(\d+s · Reading files\)/)
})

test("the verb is fixed for a turn's first seconds, then moves on, and starts elsewhere next turn", () => {
  const start = 1_700_000_000_000
  assert.equal(verbAt(start, 0), verbAt(start, 7.9))
  assert.notEqual(verbAt(start, 0), verbAt(start, 8))
  assert.notEqual(verbAt(start, 0), verbAt(start + 1000, 0))
  for (const list of [VERBS, VERBS_RU]) {
    assert.equal(new Set(list).size, list.length)
    for (const verb of list) assert.ok(verb.length <= 56 && !/[….]$/.test(verb), verb)
  }
})

test("a request with Cyrillic letters gets a Russian verb, anything else an English one", () => {
  assert.ok(VERBS_RU.includes(verbAt(1_700_000_000_000, 0, "почини парсер")))
  assert.ok(VERBS_RU.includes(verbAt(1_700_000_000_000, 0, "fix парсер")))
  assert.ok(VERBS.includes(verbAt(1_700_000_000_000, 0, "fix the parser")))
})

test("the spinner speaks the language of the request it answers", () => {
  const startedAt = Date.now() - 1000
  const state: State = { ...agent(), running: true, startedAt, items: [{ id: 1, kind: "user", text: "почини парсер" }] }
  const out = frame(createElement(ClaudeSpinner, { state }))
  assert.ok(VERBS_RU.some((verb) => out.includes(`${verb}…`)), out)
})

test("thinking says so in the parentheses", () => {
  const state: State = { ...agent(), effort: "high", running: true, startedAt: Date.now(), live: { streamId: "s", answer: "", reasoning: "", thinking: true, thinkingSince: Date.now() } }
  assert.match(frame(createElement(ClaudeSpinner, { state })), /\(\d+s · thinking with high effort\)/)
})

test("a chat reply says it is being written, not what an agent is doing", () => {
  const running = { running: true, startedAt: Date.now(), status: { text: "Working on it", step: 1, planStep: "inspect", error: false } }
  const chat = frame(createElement(ClaudeSpinner, { state: { ...agent("chat"), ...running, items: [{ id: 1, kind: "user", text: "hi" }] } }))
  assert.match(chat, /Generating a response… \(\d+s · Working on it\)/)
  const russian = frame(createElement(ClaudeSpinner, { state: { ...agent("chat"), ...running, items: [{ id: 1, kind: "user", text: "привет" }] } }))
  assert.match(russian, /Генерирую ответ…/)
  assert.ok(!VERBS.some((verb) => chat.includes(verb)))
})

test("until the router has picked a role the spinner says so, and once it has, the role decides", () => {
  const running = { running: true, startedAt: Date.now() }
  assert.match(frame(createElement(ClaudeSpinner, { state: { ...agent("auto"), ...running } })), /Picking a role…/)
  const routedChat = { ...agent("auto"), ...running, info: { ...agent("auto").info, routedRole: "chat" } }
  assert.match(frame(createElement(ClaudeSpinner, { state: routedChat })), /Generating a response…/)
  const routedCoder = { ...agent("auto"), ...running, info: { ...agent("auto").info, routedRole: "coder" } }
  const out = frame(createElement(ClaudeSpinner, { state: routedCoder }))
  assert.ok(VERBS.some((verb) => out.includes(`${verb}…`)), out)
})

test("the thought and the answer grow down from the request, and the spinner stays at the floor", async () => {
  const state: State = {
    ...agent("chat"),
    running: true,
    startedAt: Date.now(),
    live: { streamId: "s", answer: "", reasoning: "weighing the greeting", thinking: true, thinkingSince: Date.now() },
  }
  const app = render(
    createElement(
      ViewContext.Provider,
      { value: view },
      createElement(
        ClaudeScreen,
        { items: [item({ id: 1, kind: "user", text: "hello" })], showDebug: false, followKey: 0, live: createElement(ClaudeLiveBody, { state }), spinner: createElement(ClaudeSpinner, { state }) },
        createElement(Text, null, "INPUT"),
      ),
    ),
  )
  // The live text is revealed at a steady pace, so give it time to arrive.
  await new Promise((resolve) => setTimeout(resolve, 700))
  await tick()
  await tick()
  const rows = plain(app.lastFrame()).split("\n")
  app.unmount()
  const at = (pattern: RegExp) => rows.findIndex((row) => pattern.test(row))
  assert.ok(at(/❯ hello/) >= 0)
  assert.equal(at(/weighing the greeting/) - at(/❯ hello/), 2)
  assert.equal(rows.at(-1), "INPUT")
  // Blank row, the spinner and its tip: pinned right above the input, far below the thought.
  assert.equal(at(/Generating a response/), rows.length - 3)
  assert.match(rows.at(-2) ?? "", /Tip: /)
  assert.ok(at(/Generating a response/) - at(/weighing the greeting/) > 5)
})

test("a final_answer call in flight gets no line", () => {
  const state: State = {
    ...agent(),
    running: true,
    inflight: [{ step: 1, callId: "c", planStep: "done", action: "final_answer", args: {}, note: "", batchIndex: 0, at: Date.now() }],
  }
  assert.equal(frame(createElement(ClaudeLiveBody, { state })).trim(), "")
})

const finished = { seconds: 80, at: Date.UTC(2026, 8, 28, 12, 45), role: "coder", russian: false, seed: 1_700_000_000_000, interrupted: false }

test("the closing line names the time it took and when it was done, in the request's language", () => {
  assert.match(doneLine(finished), /^.+ for 1m 20s · done \d{1,2}:\d{2} [AP]M$/)
  assert.match(doneLine({ ...finished, role: "chat" }), /^Replied for 1m 20s · done /)
  assert.match(doneLine({ ...finished, russian: true }), /^.+ за 1 мин 20 с · готово \d{2}:\d{2}$/)
  assert.match(doneLine({ ...finished, role: "chat", russian: true, seconds: 4 }), /^Ответил за 4 с · готово /)
  assert.match(doneLine({ ...finished, interrupted: true }), /^Stopped after 1m 20s · \d{1,2}:\d{2} [AP]M$/)
  assert.equal(doneLine(finished), doneLine(finished))
  assert.notEqual(doneLine(finished), doneLine({ ...finished, seed: finished.seed + 1000 }))
})

test("a finished turn shows its closing line under the answer", () => {
  const out = frame(createElement(ClaudeItemView, { item: item({ kind: "done", ...finished } as Partial<Item> & { kind: Item["kind"] }), showDebug: false }))
  assert.match(out, /^\s*✻ .+ for 1m 20s · done /)
})

test("the spinner counts this turn's output tokens and mentions a thought that just ended", () => {
  const now = Date.now()
  const base = agent()
  const state: State = {
    ...base,
    running: true,
    startedAt: now - 5000,
    usage: { ...base.usage, output: 10_600 },
    turnStartOutput: 2_100,
    thought: { seconds: 2, at: now - 500 },
    status: { text: "Reading files", step: 1, planStep: "inspect", error: false },
  }
  assert.match(frame(createElement(ClaudeSpinner, { state })), /\(\d+s · ↓ 8\.5k tokens · thought for 2s\)/)
  const later: State = { ...state, thought: { seconds: 2, at: now - 5000 } }
  assert.match(frame(createElement(ClaudeSpinner, { state: later })), /\(\d+s · ↓ 8\.5k tokens · Reading files\)/)
  assert.doesNotMatch(frame(createElement(ClaudeSpinner, { state: { ...state, turnStartOutput: 10_600 } })), /tokens/)
})

test("a tip about a Kvist command sits under the spinner, in the request's language", () => {
  const running = { ...agent(), running: true, startedAt: 1_700_000_000_000 }
  const english = frame(createElement(ClaudeSpinner, { state: { ...running, items: [{ id: 1, kind: "user", text: "hello" }] } }))
  assert.ok(TIPS.some((tip) => english.includes(`Tip: ${tip.slice(0, 40)}`)), english)
  assert.match(english, /⎿ {2}Tip: /)
  const russian = frame(createElement(ClaudeSpinner, { state: { ...running, items: [{ id: 1, kind: "user", text: "привет" }] } }))
  assert.ok(TIPS_RU.some((tip) => russian.includes(`Совет: ${tip.slice(0, 30)}`)), russian)
})

test("a live thought is revealed at a steady pace instead of landing in one block", async () => {
  const reasoning = "thinking about the request ".repeat(8).trim()
  const state: State = { ...agent(), running: true, startedAt: Date.now(), live: { streamId: "s", answer: "", reasoning, thinking: true, thinkingSince: Date.now() } }
  const app = render(createElement(ViewContext.Provider, { value: view }, createElement(ClaudeLiveBody, { state })))
  const length = () => plain(app.lastFrame()).replace(/\s+/g, " ").trim().length
  await new Promise((resolve) => setTimeout(resolve, 70))
  const early = length()
  await new Promise((resolve) => setTimeout(resolve, 900))
  const late = length()
  app.unmount()
  assert.ok(early < late, `${early} should be less than ${late}`)
  assert.equal(late, reasoning.length)
})

test("the tip changes every three seconds and cycles through all of them", () => {
  const seed = 1_700_000_000_000
  assert.equal(tipFor(seed, "", 0), tipFor(seed, "", 2.9))
  assert.notEqual(tipFor(seed, "", 0), tipFor(seed, "", 3))
  const seen = new Set(Array.from({ length: TIPS.length }, (_, step) => tipFor(seed, "", step * 3)))
  assert.equal(seen.size, TIPS.length)
  const seenRu = new Set(Array.from({ length: TIPS_RU.length }, (_, step) => tipFor(seed, "привет", step * 3)))
  assert.equal(seenRu.size, TIPS_RU.length)
})

test("every tip names something the person can type", () => {
  for (const tip of [...TIPS, ...TIPS_RU]) {
    assert.ok(tip.length <= 100, tip)
    assert.match(tip, /\/[a-z]|KVIST\.md|permissions\.json|Shift|Page|Esc|Ctrl|ask again/, tip)
  }
  assert.equal(new Set(TIPS).size, TIPS.length)
  assert.equal(new Set(TIPS_RU).size, TIPS_RU.length)
})

test("the reasoning level and its command sit above the input", () => {
  assert.match(frame(createElement(ClaudeEffort, { effort: "high" })), /^\s*● high · \/effort$/)
  assert.match(frame(createElement(ClaudeEffort, { effort: "false" })), /● no reasoning · \/effort/)
  assert.equal(frame(createElement(ClaudeEffort, { effort: "" })).trim(), "")
})

test("a call still running shows Running… under it", () => {
  const state: State = {
    ...initialState(),
    running: true,
    startedAt: Date.now(),
    inflight: [{ step: 1, callId: "c", planStep: "inspect", action: "search_code", args: { query: "needle" }, note: "", batchIndex: 0, at: Date.now() }],
  }
  const out = frame(createElement(ClaudeLiveBody, { state }))
  assert.match(out, /● Search\(needle\)/)
  assert.match(out, /⎿ {2}Running…/)
})

test("a shell command in flight shows the command and how long it has run, cut after four lines", () => {
  const command = ["python3 - <<'EOF'", "a = 1", "b = 2", "c = 3", "d = 4", "e = 5"].join("\n")
  const state: State = {
    ...initialState(),
    running: true,
    startedAt: Date.now(),
    inflight: [{ step: 1, callId: "c", planStep: "verify", action: "run_shell", args: { command }, note: "", batchIndex: 0, at: Date.now() - 25_000 }],
  }
  const out = frame(createElement(ClaudeLiveBody, { state }))
  assert.match(out, /● Running shell command · 2\ds…/)
  assert.match(out, /⎿ {2}\$ python3 - <<'EOF'/)
  assert.match(out, /… \+2 lines/)
  assert.doesNotMatch(out, /d = 4/)
})
