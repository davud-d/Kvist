import assert from "node:assert/strict"
import { afterEach, test } from "node:test"

import { render } from "ink-testing-library"
import { createElement, type ReactElement } from "react"

import type { CoreEvent, JournalEvent } from "../protocol.js"
import { initialState, reduce, type Item, type State } from "../state.js"
import { ViewContext } from "../ui/context.js"
import { JobStrip } from "../ui/jobs.js"
import { ItemView } from "../ui/skins/kvist/items.js"
import { ClaudeItemView } from "../ui/skins/claude-code/claude.js"
import { applySkin } from "../ui/theme.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const view = { workspace: "/w", info: { provider: "", model: "", role: "", session: "" }, effort: "", pointer: null, expanded: false }

afterEach(() => applySkin("ink"))

function feed(state: State, ...events: CoreEvent[]): State {
  return events.reduce((acc, event, index) => reduce(acc, { type: "event", event, now: 1000 + index * 1000 }), state)
}

const started = (id: string, purpose = "", command = ["npm", "run", "dev"]): CoreEvent => ({ type: "job_started", job_id: id, command, purpose, call_id: "c1" })
const finished = (id: string, fields: Partial<Extract<CoreEvent, { type: "job_finished" }>> = {}): CoreEvent => ({ type: "job_finished", job_id: id, status: "exited", exit_code: 0, output: "", ...fields })

function frame(element: ReactElement, expanded = false): string {
  const app = render(createElement(ViewContext.Provider, { value: { ...view, expanded } }, element))
  const out = plain(app.lastFrame())
  app.unmount()
  return out
}

test("job_started adds a running job and job_output replaces its output", () => {
  const state = feed(initialState(), started("j1", "dev server"), { type: "job_output", job_id: "j1", output: "a" }, { type: "job_output", job_id: "j1", output: "b" })
  assert.deepEqual(state.jobs, [{ id: "j1", command: ["npm", "run", "dev"], purpose: "dev server", status: "running", exitCode: null, output: "b", startedAt: 1000, endedAt: null }])
  assert.equal(state.items.length, 0)
})

test("job_output for an unknown job is ignored", () => {
  assert.deepEqual(feed(initialState(), { type: "job_output", job_id: "zz", output: "x" }).jobs, [])
})

test("job_finished closes the job and appends one card with the capped output", () => {
  const state = feed(initialState(), started("j1", "build"), finished("j1", { exit_code: 2, output: "boom" }))
  const [job] = state.jobs
  assert.equal(job?.status, "exited")
  assert.equal(job?.exitCode, 2)
  assert.equal(job?.output, "boom")
  assert.equal(job?.endedAt, 2000)
  const card = state.items.at(-1)
  assert.equal(card?.kind, "job")
  assert.ok(card?.kind === "job" && card.seconds === 1 && card.exitCode === 2 && card.purpose === "build")
})

test("a finished job arriving while a call is pending does not settle the call", () => {
  const state = feed(
    initialState(),
    { type: "step", step: 1, plan_step: "edit", thought: "", user_message: "", action_type: "run_shell", action_args: { command: ["ls"] }, reasoning_seconds: 0, call_id: "c9" },
    started("j1"),
    finished("j1", { status: "stopped", exit_code: null }),
  )
  assert.equal(state.inflight.length, 1)
  assert.equal(state.jobs[0]?.status, "stopped")
})

test("job_notice becomes a dim transcript item that remembers the request's language", () => {
  const state = feed(
    reduce(initialState(), { type: "submit", text: "запусти сервер", now: 1 }),
    { type: "job_notice", text: "j1 printed new output", job_ids: ["j1"] },
  )
  const item = state.items.at(-1)
  assert.ok(item?.kind === "job_notice" && item.russian && item.jobIds[0] === "j1")
})

test("a journal replay rebuilds finished jobs, and one cut short is not left running", () => {
  const events: JournalEvent[] = [
    { type: "user", text: "go" },
    started("j1", "a"),
    finished("j1", { output: "ok" }),
    started("j2", "b"),
  ]
  const state = reduce(initialState(), { type: "restore", events, now: 5000 })
  assert.deepEqual(state.jobs.map((job) => [job.id, job.status]), [["j1", "exited"], ["j2", "stopped"]])
  assert.equal(state.items.filter((item) => item.kind === "job").length, 1)
})

test("a restore drops the jobs of the feed it replaces", () => {
  const before = feed(initialState(), started("old"))
  const restored = reduce(before, { type: "restore", events: [{ type: "turn_end" }], now: 1 })
  assert.deepEqual(restored.jobs, [])
})

test("a new session clears the jobs, while /clear keeps running ones", () => {
  const before = feed(initialState(), started("j1"))
  assert.equal(reduce(before, { type: "clear" }).jobs.length, 1)
  const next = reduce(before, { type: "event", event: { type: "command_result", name: "new", ok: true, message: "", effects: { changed: true } } })
  assert.deepEqual(next.jobs, [])
})

test("a job outlives turn_end until the core reports its end, and a dead core stops it", () => {
  const before = feed(initialState(), started("j1"))
  // An answered turn may leave the job running; its job_finished comes later, between turns.
  const answered = feed(before, { type: "turn_end" })
  assert.equal(answered.jobs[0]?.status, "running")
  assert.equal(feed(answered, { type: "job_finished", job_id: "j1", status: "exited", exit_code: 0, output: "" }).jobs[0]?.status, "exited")
  assert.equal(reduce(before, { type: "core_exit", expected: true, code: 0 }).jobs[0]?.status, "stopped")
})

test("a replayed journal leaves no job running", () => {
  const restored = reduce(initialState(), { type: "restore", events: [started("j1")], now: 1 })
  assert.equal(restored.jobs[0]?.status, "stopped")
})

test("the strip shows three running jobs, the rest as a count, and the last output line", () => {
  let state = feed(initialState(), started("a", "alpha"), started("b", "beta"), started("c", "gamma"), started("d", "delta"))
  state = feed(state, { type: "job_output", job_id: "a", output: "one\ntwo" })
  for (const skin of ["ink", "claude"] as const) {
    applySkin(skin)
    const out = frame(createElement(JobStrip, { jobs: state.jobs, russian: false }))
    assert.match(out, /alpha/)
    assert.match(out, /gamma/)
    assert.doesNotMatch(out, /delta/)
    assert.match(out, /two/)
    assert.match(out, /\+1 more/)
  }
})

test("the strip is empty when nothing runs, and speaks Russian for a Russian request", () => {
  const done = feed(initialState(), started("a"), finished("a"))
  assert.equal(frame(createElement(JobStrip, { jobs: done.jobs, russian: false })).trim(), "")
  const four = feed(initialState(), started("a"), started("b"), started("c"), started("d"))
  assert.match(frame(createElement(JobStrip, { jobs: four.jobs, russian: true })), /ещё 1/)
})

test("Ctrl+O unfolds the output tail of a running job in the claude skin", () => {
  applySkin("claude")
  const state = feed(initialState(), started("a", "alpha"), { type: "job_output", job_id: "a", output: "l1\nl2\nl3" })
  const out = frame(createElement(JobStrip, { jobs: state.jobs, russian: false }), true)
  assert.match(out, /l1/)
  assert.match(out, /l3/)
})

const card = (fields: Partial<Extract<Item, { kind: "job" }>> = {}): Item => ({ id: 1, kind: "job", jobId: "j1", command: ["make"], purpose: "build", status: "exited", exitCode: 0, output: "a\nb\nc\nd\ne", seconds: 12, russian: false, ...fields })

test("kvist skin: a finished job is a marker, the way it ended and its tail", () => {
  const ok = frame(createElement(ItemView, { item: card(), showDebug: false }))
  assert.match(ok, /✓ build · exit code 0 · 12s/)
  assert.match(ok, /e/)
  const failed = frame(createElement(ItemView, { item: card({ exitCode: 3, output: "" }), showDebug: false }))
  assert.match(failed, /✗ build · exit code 3/)
  assert.match(failed, /\(no output\)/)
  assert.match(frame(createElement(ItemView, { item: card({ status: "timed_out", exitCode: null, russian: true }), showDebug: false })), /время вышло/)
})

test("claude skin: a finished job is a call with its result hanging off it, folded to three lines", () => {
  applySkin("claude")
  const folded = frame(createElement(ClaudeItemView, { item: card(), showDebug: false }))
  assert.match(folded, /● build/)
  assert.match(folded, /⎿ {2}exit code 0 · 12s/)
  assert.match(folded, /earlier lines/)
  assert.doesNotMatch(folded, /\ba\b/)
  const open = frame(createElement(ClaudeItemView, { item: card(), showDebug: false }), true)
  assert.match(open, /\ba\b/)
})

test("both skins show a notice as a dim 'model was told' line with a short preview", () => {
  const notice: Item = { id: 2, kind: "job_notice", text: "j1 has new output\nsecond line", jobIds: ["j1"], russian: false }
  assert.match(frame(createElement(ItemView, { item: notice, showDebug: false })), /model was told: j1 has new output …/)
  applySkin("claude")
  assert.match(frame(createElement(ClaudeItemView, { item: notice, showDebug: false })), /model was told: j1 has new output …/)
})

test("a background run_shell call is labelled in both skins", () => {
  const call: Item = { id: 3, kind: "tool", step: 1, planStep: "edit", action: "run_shell", args: { command: ["npm", "run", "dev"], background: true }, result: { ok: true, summary: "started", output: "", exit_code: null, path: null, first_line: null }, batchIndex: 0 }
  assert.match(frame(createElement(ItemView, { item: call, showDebug: false })), /\$ npm run dev \(background\)/)
  applySkin("claude")
  assert.match(frame(createElement(ClaudeItemView, { item: call, showDebug: false })), /Bash \(background\)\(npm run dev\)/)
})
