import { clipText, englishOnly } from "./format.js"
import type { CoreEvent, InputRequest, JournalEvent } from "./protocol.js"

export type ActionResult = Omit<Extract<CoreEvent, { type: "action_result" }>, "type" | "step" | "action_type">

export type Tone = "info" | "warn" | "error" | "debug"

export type Item =
  | { id: number; kind: "banner" }
  // `request` is the full text when `text` shows a paste label; the language of the turn comes from it.
  | { id: number; kind: "user"; text: string; request?: string }
  // The plan the model proposes before editing; `russian` picks the card's label, like `done`.
  | { id: number; kind: "plan"; markdown: string; russian: boolean }
  // batchIndex: the call's place in its model response (0 = the first call), so a UI can
  // show the calls of one response as a group.
  | { id: number; kind: "step"; step: number; planStep: string; action: string; target: string; note: string; batchIndex: number }
  | { id: number; kind: "tool"; step: number; planStep: string; action: string; args: Record<string, unknown>; result: ActionResult; batchIndex: number }
  | { id: number; kind: "thought"; seconds: number; text: string }
  | { id: number; kind: "explored"; counts: ExploreCounts; names: string[] }
  // fresh: it arrived while a turn ran, so a view may reveal it gradually; replayed history is not.
  | { id: number; kind: "answer"; text: string; done: boolean; fresh?: boolean }
  | { id: number; kind: "note"; tone: Tone; text: string }
  // The closing line of a live turn: how long it ran and when it ended. `role`, `russian` and
  // `seed` (the turn's start) let a view word it; a replayed journal has no such line.
  | { id: number; kind: "done"; seconds: number; at: number; role: string; russian: boolean; seed: number; interrupted: boolean }
  // The words a model response carries for the person, shown once, before the calls
  // of that response.
  | { id: number; kind: "say"; text: string; fresh?: boolean }
  // A background job ended. `seconds` is how long it ran; `russian` picks the card's words.
  | { id: number; kind: "job"; jobId: string; command: string[]; purpose: string; status: JobEnd; exitCode: number | null; output: string; seconds: number; russian: boolean }
  // What the model was told about background jobs before a request, shown dimmed.
  | { id: number; kind: "job_notice"; text: string; jobIds: string[]; russian: boolean }

export type JobEnd = Extract<CoreEvent, { type: "job_finished" }>["status"]

// A background shell command: running until a job_finished event arrives.
export type JobView = {
  id: string
  command: string[]
  purpose: string
  status: "running" | "exited" | "timed_out" | "stopped"
  exitCode: number | null
  output: string
  startedAt: number
  endedAt: number | null
}

export interface ExploreCounts {
  reads: number
  searches: number
  lists: number
  git: number
}

// An action that has started and has no result yet. Static items cannot change
// once printed, so it waits here and is committed once, together with its
// result, as a single card. Several can wait at once: the read-only calls of
// one response run in parallel.
export interface Inflight {
  step: number
  callId: string
  planStep: string
  action: string
  args: Record<string, unknown>
  note: string
  batchIndex: number
  // When the call started, for "Running … · 25s".
  at: number
}

// A read-only step that may still fail; its result arrives after it, possibly
// after the other reads of the same response have started.
interface ExploreStep {
  step: number
  callId: string
  kind: keyof ExploreCounts
  weight: number
  label: string
}

export interface Live {
  streamId: string
  answer: string
  reasoning: string
  thinking: boolean
  thinkingSince: number | null
}

export interface State {
  nextId: number
  epoch: number
  items: Item[]
  live: Live | null
  // A run of read-only steps still growing. Static items cannot change once
  // printed, so the group lives here and is committed as one item when
  // anything else arrives.
  exploring: ExploreCounts | null
  // What the group above read or searched, one label per step, for a UI that lists them.
  exploringNames: string[]
  exploreSteps: ExploreStep[]
  inflight: Inflight[]
  // The model response the latest step came from, and how many of its calls arrived.
  batch: { id: string; count: number } | null
  running: boolean
  status: { text: string; step: number; planStep: string; error: boolean } | null
  pending: InputRequest | null
  // The slash command the core is running; the input stays locked meanwhile.
  command: string | null
  // The level `hello` announced, then the last `/effort` result.
  effort: string
  // The core's notice when end_session closed the session on screen; it takes the input's
  // place until another session is opened.
  closed: string | null
  // Background jobs of the session on screen, oldest first; finished ones stay until a new session.
  jobs: JobView[]
  info: { provider: string; model: string; role: string; session: string; title?: string; routedRole?: string; kvistVersion?: string }
  usage: { input: number; output: number; context: number; limit: number }
  showDebug: boolean
  startedAt: number | null
  // The person stopped the running turn; the closing line then says so.
  interrupted: boolean
  // When the model's current response began to be awaited: the turn start, then each finished
  // model call or action result. The spinner warms up the longer this stays unchanged.
  waitingSince: number | null
  // The output-token total when this turn began, so the turn's own count is `usage.output` minus this.
  turnStartOutput: number
  // The thought that just ended, for the spinner to mention for a moment.
  thought: { seconds: number; at: number } | null
  // Items up to this id were redrawn from a session's journal rather than seen live, so a UI
  // can skip the entrance effects meant for fresh work.
  settledThrough: number
  // The task's lifecycle state from the core: running, pause_requested, paused, cancelling, ...
  taskState: string
  // The agent's own screen while a skill shows one: its title, state and local viewer link.
  screen: { title: string; state: string; url: string; stream: string } | null
  // True right after a reasoning_commit, until the very next event. The step that
  // immediately follows already narrated its thinking on that stream, so it must not
  // print its own short `thought` field as a second, near-duplicate card.
  justReasoned: boolean
}

export type Action =
  | { type: "event"; event: CoreEvent; now?: number }
  // `banner`: start the redrawn feed with the welcome block, as a new session does.
  | { type: "restore"; events: JournalEvent[]; now?: number; banner?: boolean }
  | { type: "submit"; text: string; request?: string; now?: number }
  // A slash command typed by the person, shown like a request but not starting a run.
  | { type: "echo"; text: string }
  // The person asked the running turn to stop.
  | { type: "interrupt" }
  | { type: "command_start"; name: string }
  | { type: "resume_start"; now?: number }
  | { type: "note"; tone: Tone; text: string }
  | { type: "answered" }
  | { type: "core_exit"; expected: boolean; code: number | null }
  | { type: "toggle_debug" }
  | { type: "clear" }

export function initialState(effort = "", banner = false): State {
  return {
    nextId: banner ? 2 : 1,
    epoch: 0,
    items: banner ? [{ id: 1, kind: "banner" }] : [],
    live: null,
    exploring: null,
    exploringNames: [],
    exploreSteps: [],
    inflight: [],
    batch: null,
    running: false,
    status: null,
    pending: null,
    command: null,
    effort,
    closed: null,
    jobs: [],
    info: { provider: "", model: "", role: "", session: "" },
    usage: { input: 0, output: 0, context: 0, limit: 0 },
    showDebug: false,
    startedAt: null,
    waitingSince: null,
    interrupted: false,
    turnStartOutput: 0,
    thought: null,
    settledThrough: 0,
    justReasoned: false,
    taskState: "idle",
    screen: null,
  }
}

const TARGET_KEYS = ["path", "command", "query", "pattern", "url", "dir", "directory", "job_id"] as const

// The one argument that says what an action is about, shown next to its name.
export function actionTarget(args: Record<string, unknown>): string {
  for (const key of TARGET_KEYS) {
    const value = args[key]
    if (typeof value === "string" && value) return value
    if (Array.isArray(value) && value.length) return value.join(" ")
  }
  const paths = args.paths
  if (Array.isArray(paths) && paths.length) return paths.map(String).join(", ")
  return ""
}

const EXPLORE_KIND: Record<string, keyof ExploreCounts> = {
  read_file: "reads",
  read_multiple_files: "reads",
  search_code: "searches",
  find_files: "searches",
  list_dir: "lists",
  list_tree: "lists",
  git_status: "git",
}

const EXPLORE_WORDS: readonly [keyof ExploreCounts, string, string][] = [
  ["reads", "read", "reads"],
  ["searches", "search", "searches"],
  ["lists", "listing", "listings"],
  ["git", "git check", "git checks"],
]

export function exploreSummary(counts: ExploreCounts): string {
  return EXPLORE_WORDS.filter(([key]) => counts[key] > 0)
    .map(([key, one, many]) => `${counts[key]} ${counts[key] === 1 ? one : many}`)
    .join(", ")
}

function exploreWeight(kind: keyof ExploreCounts, args: Record<string, unknown>): number {
  return kind === "reads" && Array.isArray(args.paths) && args.paths.length ? args.paths.length : 1
}

function explore(state: State, kind: keyof ExploreCounts, weight: number, label: string): State {
  const counts = state.exploring ?? { reads: 0, searches: 0, lists: 0, git: 0 }
  return { ...state, exploring: { ...counts, [kind]: counts[kind] + weight }, exploringNames: [...state.exploringNames, label] }
}

function flushExploring(state: State): State {
  if (!state.exploring) return state
  const item: Item = { id: state.nextId, kind: "explored", counts: state.exploring, names: state.exploringNames }
  return { ...state, exploring: null, exploringNames: [], items: [...state.items, item], nextId: state.nextId + 1 }
}

// The step line an action gets when no result ever arrives for it (blocked by
// the runtime, or the turn ended first).
function commitInflight(state: State): State {
  const items = state.inflight.map((call, index): Item => ({
    id: state.nextId + index,
    kind: "step",
    step: call.step,
    planStep: call.planStep,
    action: call.action,
    target: actionTarget(call.args),
    note: call.note,
    batchIndex: call.batchIndex,
  }))
  if (!items.length) return state
  return { ...state, inflight: [], items: [...state.items, ...items], nextId: state.nextId + items.length }
}

// The newest call still running, for a UI that shows one.
export function lastInflight(state: State): Inflight | null {
  return state.inflight.at(-1) ?? null
}

// Order matters: the read group started before the pending action did.
function settle(state: State): State {
  return commitInflight(flushExploring(state))
}

// A note (stderr chatter, a sandbox notice, an error line) can arrive while an
// action is still running; if it settled the pending step, that step would be
// printed as a bare line and its result card would be lost.
function push(state: State, item: (id: number) => Item): State {
  const settled = item(state.nextId).kind === "note" ? state : settle(state)
  return append(settled, item)
}

function append(state: State, item: (id: number) => Item): State {
  return { ...state, items: [...state.items, item(state.nextId)], nextId: state.nextId + 1 }
}

// A result names its call; a core older than call ids only names its step.
function answers(call: { step: number; callId: string }, event: { step: number; call_id?: string }): boolean {
  return call.callId && event.call_id ? call.callId === event.call_id : call.step === event.step
}

function withoutCount(counts: ExploreCounts, step: ExploreStep): ExploreCounts | null {
  const next = { ...counts, [step.kind]: Math.max(0, counts[step.kind] - step.weight) }
  return Object.values(next).some((n) => n > 0) ? next : null
}

// A failed read must not vanish into "Explored – 3 reads": it ends the group
// and gets its own line.
function failExploreStep(state: State, step: ExploreStep, summary: string): State {
  const remaining = state.exploring ? withoutCount(state.exploring, step) : null
  const flushed = flushExploring({ ...state, exploring: remaining })
  const text = `${step.label}: ${summary}`
  return push(flushed, (id) => ({ id, kind: "note", tone: "error", text }))
}

function onActionResult(state: State, event: Extract<CoreEvent, { type: "action_result" }>): State {
  const result: ActionResult = { ok: event.ok, summary: event.summary, output: event.output, exit_code: event.exit_code, path: event.path, first_line: event.first_line }
  const call = state.inflight.find((waiting) => answers(waiting, event))
  if (call) {
    // The other calls of the response may still be running: only the read group
    // before this card settles, never they.
    const done = flushExploring({ ...state, inflight: state.inflight.filter((waiting) => waiting !== call) })
    return append(done, (id) => ({ id, kind: "tool", step: call.step, planStep: call.planStep, action: call.action, args: call.args, result, batchIndex: call.batchIndex }))
  }
  const read = state.exploreSteps.find((waiting) => answers(waiting, event))
  if (!read) return state
  const pending = { ...state, exploreSteps: state.exploreSteps.filter((waiting) => waiting !== read) }
  return event.ok ? pending : failExploreStep(pending, read, event.summary)
}

function ensureLive(state: State, streamId: string): Live {
  if (state.live && state.live.streamId === streamId) return state.live
  return { streamId, answer: "", reasoning: "", thinking: false, thinkingSince: null }
}

function commitLive(state: State, text: string): State {
  const next = { ...state, live: null }
  if (!text.trim()) return next
  return push(next, (id) => ({ id, kind: "answer", text, done: true, fresh: state.running }))
}

function lastAnswerText(state: State): string | null {
  const last = state.items.at(-1)
  return last?.kind === "answer" ? last.text.trim() : null
}

// The `fromEnd`-th answer counting back from the most recent one (1 = last).
// Plain text as the model produced it: no ANSI, no UI chrome, safe to copy.
export function answerFromEnd(items: readonly Item[], fromEnd: number): string | null {
  let seen = 0
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (!item || item.kind !== "answer") continue
    seen += 1
    if (seen === fromEnd) return item.text.trim()
  }
  return null
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

// What a finished command changed, mirrored into what the UI shows.
function applyEffects(state: State, effects: Record<string, unknown>): State {
  const info = { ...state.info }
  const role = asText(effects.role)
  if (role) {
    info.role = role
    info.routedRole = undefined
  }
  const provider = asText(effects.provider)
  if (provider) info.provider = provider
  const model = asText(record(effects.models).fast)
  if (model) info.model = model
  const session = asText(effects.session)
  if (session) info.session = session
  const title = asText(effects.title)
  if (title) info.title = title
  return { ...state, info, effort: asText(effects.effort) || state.effort }
}

const REPORT_LINES = 40

function commandNotes(state: State, event: Extract<CoreEvent, { type: "command_result" }>): State {
  const effects = record(event.effects)
  // A /changes report is file names and diffs, not a bilingual sentence.
  const raw = event.name === "changes" ? event.message : englishOnly(event.message)
  let next = raw.trim() ? push(state, (id) => ({ id, kind: "note", tone: "info", text: clipText(raw, REPORT_LINES) })) : state
  const advisory = englishOnly(asText(effects.advisory)).trim()
  if (advisory && !raw.includes(advisory)) next = push(next, (id) => ({ id, kind: "note", tone: "info", text: advisory }))
  const warnings = Array.isArray(effects.warnings) ? effects.warnings.filter((w): w is string => typeof w === "string" && w.trim() !== "") : []
  return warnings.reduce((acc, warning) => push(acc, (id) => ({ id, kind: "note", tone: "warn", text: englishOnly(warning) })), next)
}

function onCommandResult(state: State, event: Extract<CoreEvent, { type: "command_result" }>): State {
  const done = { ...state, command: null, pending: null }
  if (!event.ok) {
    const reason = englishOnly(event.message).trim() || `/${event.name} failed`
    return push(done, (id) => ({ id, kind: "note", tone: "error", text: reason }))
  }
  const effects = record(event.effects)
  // A cancelled dialog changed nothing, so there is nothing to report.
  if (effects.cancelled === true) return done
  const applied = applyEffects(done, effects)
  // /new and /session: what is on screen belongs to the session that was left.
  return commandNotes(effects.changed === true ? beginSession(applied) : applied, event)
}

// An empty feed under the welcome block, and a context that starts from nothing.
function beginSession(state: State): State {
  const cleared = reduce(state, { type: "clear" })
  const fresh = { ...cleared, jobs: [], usage: { ...cleared.usage, context: 0 } }
  return push(fresh, (id) => ({ id, kind: "banner" }))
}

// The core ends every job before turn_end (or with the core itself), so one still running here
// belongs to a journal cut short or a dead core and must not stay "running" on screen.
function stopRunning(jobs: JobView[], now: number): JobView[] {
  if (!jobs.some((job) => job.status === "running")) return jobs
  return jobs.map((job) => (job.status === "running" ? { ...job, status: "stopped", endedAt: now } : job))
}

// A job's end is also a transcript card. It is appended without settling the pending calls:
// jobs end while other calls of the same response are still waiting for their results.
function onJobFinished(state: State, event: Extract<CoreEvent, { type: "job_finished" }>, now: number): State {
  const known = state.jobs.find((job) => job.id === event.job_id)
  const base: JobView = known ?? { id: event.job_id, command: [], purpose: "", status: "running", exitCode: null, output: "", startedAt: now, endedAt: null }
  const done: JobView = { ...base, status: event.status, exitCode: event.exit_code ?? null, output: event.output ?? "", endedAt: now }
  const jobs = known ? state.jobs.map((job) => (job === known ? done : job)) : [...state.jobs, done]
  const seconds = Math.max(0, (now - done.startedAt) / 1000)
  const card = (id: number): Item => ({ id, kind: "job", jobId: done.id, command: done.command, purpose: done.purpose, status: event.status, exitCode: done.exitCode, output: done.output, seconds, russian: requestIsRussian(state) })
  return append({ ...state, jobs }, card)
}

// `justReasoned` must survive only from a reasoning_commit to the single event right
// after it (see the field's own comment); every other event type clears it, so a
// leftover `true` from an earlier, unrelated commit can never suppress a later step's
// own thought.
function onEvent(state: State, event: CoreEvent, now: number): State {
  const next = applyEvent(state, event, now)
  if (event.type === "reasoning_start" || event.type === "reasoning_chunk" || event.type === "reasoning_commit" || event.type === "step") {
    return next
  }
  return next.justReasoned ? { ...next, justReasoned: false } : next
}

function applyEvent(state: State, event: CoreEvent, now: number): State {
  switch (event.type) {
    case "hello": {
      const models = event.models ?? {}
      const model = models.fast ?? models.default ?? Object.values(models)[0] ?? ""
      const info = {
        provider: event.provider ?? "",
        model,
        role: event.role ?? state.info.role,
        session: event.session ?? "",
        kvistVersion: event.kvist_version ?? state.info.kvistVersion,
      }
      // Any earlier routed role belonged to a core that is gone.
      const first = state.items.length === 0
      const next = { ...state, info, effort: event.effort || state.effort }
      return first ? push(next, (id) => ({ id, kind: "banner" })) : next
    }
    case "live_start":
      return { ...state, running: true }
    case "live_stop":
      return { ...state, status: null }
    case "status":
      return {
        ...state,
        status: { text: event.user_message || event.status, step: event.step, planStep: event.plan_step, error: event.is_error },
      }
    case "plan":
      return push(state, (id) => ({ id, kind: "plan", markdown: event.markdown, russian: requestIsRussian(state) }))
    case "route":
      return event.role ? { ...state, info: { ...state.info, routedRole: event.role } } : state
    case "step": {
      const args = event.action_args ?? {}
      const kind = EXPLORE_KIND[event.action_type]
      const seconds = event.reasoning_seconds || 0
      const text = (event.thought ?? "").trim()
      const callId = event.call_id ?? ""
      const batchId = event.batch_id ?? ""
      // The calls of one response arrive one after another before their results; a new
      // response means whatever is still waiting from the last one never got a result.
      const sameBatch = batchId !== "" && state.batch?.id === batchId
      const batchIndex = sameBatch ? (state.batch?.count ?? 0) : 0
      const batch = { id: batchId, count: batchIndex + 1 }
      // A step right after a streamed reasoning_commit already narrated its thinking on
      // that stream; its own short `thought` field would only repeat it as a second,
      // near-duplicate card. A routine note on a read-only step would also end its
      // group after every read, so only real thinking time earns a break there. The
      // thinking belongs to the response, so only its first call shows it.
      const alreadyNarrated = state.justReasoned
      const shown = batchIndex === 0 && (kind ? seconds > 0 : !alreadyNarrated && (Boolean(text) || seconds > 0))
      const settled = state.inflight.length && !sameBatch ? settle(state) : state
      const withThought = shown ? push(settled, (id) => ({ id, kind: "thought", seconds, text })) : settled
      // The response's words precede the calls they introduce, reads included.
      const say = event.user_message.trim()
      const next = say ? push(withThought, (id) => ({ id, kind: "say", text: say, fresh: state.running })) : withThought
      if (kind) {
        const weight = exploreWeight(kind, args)
        const label = `${event.action_type}  ${actionTarget(args)}`.trim()
        const read: ExploreStep = { step: event.step, callId, kind, weight, label }
        return { ...explore(next, kind, weight, label), exploreSteps: [...next.exploreSteps, read], batch, justReasoned: false }
      }
      // A writing call runs alone, so the read group before it is complete.
      const flushed = flushExploring(next)
      const call: Inflight = { step: event.step, callId, planStep: event.plan_step, action: event.action_type, args, note: event.action_description ?? "", batchIndex, at: now }
      return { ...flushed, inflight: [...flushed.inflight, call], batch, justReasoned: false }
    }
    case "action_result":
      return onActionResult(state, event)
    case "sandbox_notice":
      return push(state, (id) => ({ id, kind: "note", tone: "info", text: event.note }))
    case "stream_start":
      return { ...state, live: ensureLive(state, event.stream_id) }
    case "stream_chunk": {
      const current = ensureLive(state, event.stream_id)
      const answer = event.append ? current.answer + event.text : event.text
      return { ...state, live: { ...current, answer } }
    }
    case "stream_commit":
      return commitLive(state, event.text)
    case "reasoning_start":
      return { ...state, live: { ...ensureLive(state, event.stream_id), thinking: true, thinkingSince: now } }
    case "reasoning_chunk": {
      const current = ensureLive(state, event.stream_id)
      const reasoning = event.append ? current.reasoning + event.text : event.text
      return { ...state, live: { ...current, thinking: true, reasoning } }
    }
    case "reasoning_commit": {
      const current = ensureLive(state, event.stream_id)
      const next = { ...state, live: { ...current, thinking: false, thinkingSince: null, reasoning: "" } }
      const text = current.reasoning.trim()
      const result = event.duration_seconds >= 1 || text
        ? push(next, (id) => ({ id, kind: "thought", seconds: event.duration_seconds, text }))
        : next
      return { ...result, justReasoned: true, thought: event.duration_seconds >= 1 ? { seconds: event.duration_seconds, at: now } : null }
    }
    case "message":
      return push(state, (id) => ({ id, kind: "answer", text: event.markdown, done: true, fresh: state.running }))
    case "final": {
      // A streamed reply and its `final` carry the same text: show it once.
      if (lastAnswerText(state) === event.message.trim()) return state
      return push(state, (id) => ({ id, kind: "answer", text: event.message, done: true, fresh: state.running }))
    }
    case "usage": {
      const u = state.usage
      return {
        ...state,
        usage: {
          input: u.input + (event.input_tokens || 0),
          output: u.output + (event.output_tokens || 0),
          context: event.context_tokens,
          limit: event.context_limit,
        },
      }
    }
    case "context":
      return { ...state, usage: { ...state.usage, context: event.context_tokens, limit: event.context_limit } }
    case "debug":
      return push(state, (id) => ({ id, kind: "note", tone: "debug", text: event.message }))
    case "error":
      return push(state, (id) => ({ id, kind: "note", tone: "error", text: event.message }))
    case "input_request":
      return { ...state, pending: event }
    case "command_result":
      return onCommandResult(state, event)
    case "session":
      return { ...state, info: { ...state.info, session: event.session, title: event.title || undefined }, closed: event.closed === true ? (event.closed_notice ?? "") : null }
    case "session_title":
      // A title can land after the person moved to another session.
      return event.session === state.info.session ? { ...state, info: { ...state.info, title: event.title } } : state
    case "session_ended":
      return { ...state, closed: event.closed_notice }
    case "job_started": {
      const job: JobView = { id: event.job_id, command: event.command ?? [], purpose: event.purpose ?? "", status: "running", exitCode: null, output: "", startedAt: now, endedAt: null }
      return { ...state, jobs: [...state.jobs.filter((known) => known.id !== job.id), job] }
    }
    case "job_output":
      return { ...state, jobs: state.jobs.map((job) => (job.id === event.job_id ? { ...job, output: event.output } : job)) }
    case "job_finished":
      return onJobFinished(state, event, now)
    case "job_notice":
      return append(state, (id) => ({ id, kind: "job_notice", text: event.text, jobIds: event.job_ids ?? [], russian: requestIsRussian(state) }))
    case "task_state":
      return { ...state, taskState: event.state }
    case "screen":
      return { ...state, screen: event.state === "closed" || !event.url ? null : { title: event.title, state: event.state, url: event.url, stream: event.stream_url } }
    case "skill":
      return push(state, (id) => ({ id, kind: "note", tone: "info", text: skillNote(event, requestIsRussian(state)) }))
    case "helper":
      return event.event === "update_applied" ? state : push(state, (id) => ({ id, kind: "note", tone: event.event === "failed" ? "warn" : "info", text: helperNote(event, requestIsRussian(state)) }))
    case "artifact":
      return push(state, (id) => ({ id, kind: "note", tone: "info", text: `${event.caption || event.name}: ${event.path}` }))
    case "turn_end":
      // `command` is cleared too: `resume` is dispatched as a command but ends
      // as a turn, and a core that answers any command this way must not leave
      // the input box disabled forever.
      return { ...settle(state), jobs: state.taskState === "paused" ? state.jobs : stopRunning(state.jobs, now), exploreSteps: [], batch: null, running: false, live: null, status: null, pending: null, startedAt: null, waitingSince: null, interrupted: false, thought: null, command: null, screen: state.taskState === "paused" ? state.screen : null }
    default:
      return state
  }
}

function skillNote(event: Extract<CoreEvent, { type: "skill" }>, russian: boolean): string {
  if (russian) return `Скилл «${event.name}» ${event.version} ${event.state === "attached" ? "подключён" : "отключён"}`
  return `Skill "${event.name}" ${event.version} ${event.state}`
}

function helperNote(event: Extract<CoreEvent, { type: "helper" }>, russian: boolean): string {
  const by = event.stopped_by === "person" ? (russian ? " вами" : " by you") : ""
  const words: Record<string, [string, string]> = {
    started: ["запущен", "started"], done: ["закончил", "finished"], failed: ["завершился ошибкой", "failed"],
    stopped: ["остановлен", "stopped"], paused: ["на паузе", "paused"],
  }
  const [ru, en] = words[event.event] ?? [event.event, event.event]
  const head = russian ? `Помощник ${event.agent_id} ${ru}${by}` : `Helper ${event.agent_id} ${en}${by}`
  const detail = event.summary || (event.event === "started" ? event.task : "")
  return detail ? `${head}: ${detail}` : head
}

function noticeText(event: Extract<JournalEvent, { type: "journal_notice" }>): string {
  if (event.omitted_turns) return `${event.omitted_turns} older turns are not shown.`
  if (event.omitted_events) return `${event.omitted_events} events of this turn are not shown.`
  // A notice with no count is the caller saying the journal it was given starts mid-way.
  return "Earlier events are not shown."
}

// A session redrawn from its journal, through the reducer live events use. Token counts are
// skipped: they describe the conversation now running, not the one being shown.
function restore(state: State, events: JournalEvent[], now: number, banner: boolean): State {
  let next = banner ? beginSession(state) : { ...reduce(state, { type: "clear" }), jobs: [] }
  for (const event of events) {
    if (event.type === "usage") continue
    if (event.type === "user") {
      // A resumed turn has no request text of its own.
      if (event.text) next = reduce(next, { type: "submit", text: event.text, now })
      continue
    }
    if (event.type === "journal_notice") {
      next = push(next, (id) => ({ id, kind: "note", tone: "info", text: noticeText(event) }))
      continue
    }
    next = onEvent(next, event, now)
  }
  // A journal cut off by a crash has no closing turn_end.
  const settled = onEvent(next, { type: "turn_end" }, now)
  return { ...settled, info: { ...settled.info, routedRole: undefined }, settledThrough: settled.nextId - 1 }
}

const CYRILLIC = /\p{Script=Cyrillic}/u

// The request on screen decides the language of cards that have no text of their own.
export function requestIsRussian(state: State): boolean {
  const request = state.items.findLast((item) => item.kind === "user")
  return request?.kind === "user" && CYRILLIC.test(request.request ?? request.text)
}

// A turn that ran live ends with its closing line; `before` still knows when it began.
function closeTurn(before: State, after: State, at: number): State {
  if (before.startedAt === null) return after
  return push(after, (id) => ({
    id,
    kind: "done",
    seconds: Math.max(0, (at - before.startedAt!) / 1000),
    at,
    role: before.info.routedRole ?? before.info.role,
    russian: requestIsRussian(before),
    seed: before.startedAt!,
    interrupted: before.interrupted,
  }))
}

export function reduce(state: State, action: Action): State {
  switch (action.type) {
    case "event": {
      const at = action.now ?? Date.now()
      const next = onEvent(state, action.event, at)
      if (action.event.type === "turn_end") return closeTurn(state, next, at)
      const answered = action.event.type === "usage" || action.event.type === "action_result"
      return answered ? { ...next, waitingSince: at } : next
    }
    case "restore":
      return restore(state, action.events, action.now ?? Date.now(), action.banner ?? false)
    case "submit":
      return push(
        { ...state, running: true, startedAt: action.now ?? Date.now(), waitingSince: action.now ?? Date.now(), turnStartOutput: state.usage.output, thought: null, info: { ...state.info, routedRole: undefined } },
        (id) => ({ id, kind: "user", text: action.text, request: action.request }),
      )
    case "interrupt":
      return { ...state, interrupted: true }
    case "echo":
      return push(state, (id) => ({ id, kind: "user", text: action.text }))
    case "command_start":
      return { ...state, command: action.name }
    case "resume_start":
      return { ...state, running: true, startedAt: action.now ?? Date.now(), waitingSince: action.now ?? Date.now(), turnStartOutput: state.usage.output, thought: null }
    case "note":
      return push(state, (id) => ({ id, kind: "note", tone: action.tone, text: action.text }))
    case "answered":
      return { ...state, pending: null }
    case "core_exit": {
      const next = { ...settle(state), jobs: stopRunning(state.jobs, Date.now()), exploreSteps: [], batch: null, running: false, live: null, status: null, pending: null, command: null }
      if (action.expected) return next
      return push(next, (id) => ({ id, kind: "note", tone: "error", text: `The Kvist core stopped unexpectedly (exit code ${action.code ?? "?"}).` }))
    }
    case "toggle_debug":
      return { ...state, showDebug: !state.showDebug }
    case "clear":
      // Jobs are kept: /clear empties the screen, and a running job is still running.
      return { ...state, items: [], exploring: null, exploringNames: [], exploreSteps: [], inflight: [], batch: null, justReasoned: false, epoch: state.epoch + 1 }
  }
}
