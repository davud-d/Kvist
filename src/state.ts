import { clipText, englishOnly } from "./format.js"
import type { CoreEvent, InputRequest, JournalEvent } from "./protocol.js"

export type ActionResult = Omit<Extract<CoreEvent, { type: "action_result" }>, "type" | "step" | "action_type">

export type Tone = "info" | "warn" | "error" | "debug"

export type Item =
  | { id: number; kind: "banner" }
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "plan"; complexity: string; steps: string[]; risks: string[] }
  | { id: number; kind: "step"; step: number; planStep: string; action: string; target: string; note: string }
  | { id: number; kind: "tool"; step: number; planStep: string; action: string; args: Record<string, unknown>; result: ActionResult }
  | { id: number; kind: "thought"; seconds: number; text: string }
  | { id: number; kind: "explored"; counts: ExploreCounts; names: string[] }
  | { id: number; kind: "answer"; text: string; done: boolean }
  | { id: number; kind: "note"; tone: Tone; text: string }
  // The model's user-facing one-liner for a step ("Reading the target file"), shown
  // before the action it describes. Explore-kind steps skip it: they are already
  // folded into one "Explored" line, and a comment per read would defeat that.
  | { id: number; kind: "say"; text: string }

export interface ExploreCounts {
  reads: number
  searches: number
  lists: number
  git: number
}

// An action that has started and has no result yet. Static items cannot change
// once printed, so it waits here and is committed once, together with its
// result, as a single card.
export interface Inflight {
  step: number
  planStep: string
  action: string
  args: Record<string, unknown>
  note: string
}

// The read-only step that may still fail: its result arrives right after it.
interface ExploreStep {
  step: number
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
  exploreStep: ExploreStep | null
  inflight: Inflight | null
  running: boolean
  status: { text: string; step: number; planStep: string; error: boolean } | null
  pending: InputRequest | null
  // The slash command the core is running; the input stays locked meanwhile.
  command: string | null
  // The last `/effort` result; the core does not announce it in `hello`.
  effort: string
  info: { provider: string; model: string; role: string; session: string; routedRole?: string; kvistVersion?: string }
  usage: { input: number; output: number; context: number; limit: number }
  showDebug: boolean
  startedAt: number | null
  // Items up to this id were redrawn from a session's journal rather than seen live, so a UI
  // can skip the entrance effects meant for fresh work.
  settledThrough: number
  // True right after a reasoning_commit, until the very next event. The step that
  // immediately follows already narrated its thinking on that stream, so it must not
  // print its own short `thought` field as a second, near-duplicate card.
  justReasoned: boolean
}

export type Action =
  | { type: "event"; event: CoreEvent; now?: number }
  | { type: "restore"; events: JournalEvent[]; now?: number }
  | { type: "submit"; text: string; now?: number }
  | { type: "command_start"; name: string }
  | { type: "resume_start"; now?: number }
  | { type: "note"; tone: Tone; text: string }
  | { type: "answered" }
  | { type: "core_exit"; expected: boolean; code: number | null }
  | { type: "toggle_debug" }
  | { type: "clear" }

export function initialState(effort = ""): State {
  return {
    nextId: 1,
    epoch: 0,
    items: [],
    live: null,
    exploring: null,
    exploringNames: [],
    exploreStep: null,
    inflight: null,
    running: false,
    status: null,
    pending: null,
    command: null,
    effort,
    info: { provider: "", model: "", role: "", session: "" },
    usage: { input: 0, output: 0, context: 0, limit: 0 },
    showDebug: false,
    startedAt: null,
    settledThrough: 0,
    justReasoned: false,
  }
}

const TARGET_KEYS = ["path", "command", "query", "pattern", "url", "dir", "directory"] as const

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
  const { inflight } = state
  if (!inflight) return state
  const item: Item = {
    id: state.nextId,
    kind: "step",
    step: inflight.step,
    planStep: inflight.planStep,
    action: inflight.action,
    target: actionTarget(inflight.args),
    note: inflight.note,
  }
  return { ...state, inflight: null, items: [...state.items, item], nextId: state.nextId + 1 }
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
  return { ...settled, items: [...settled.items, item(settled.nextId)], nextId: settled.nextId + 1 }
}

function withoutCount(counts: ExploreCounts, step: ExploreStep): ExploreCounts | null {
  const next = { ...counts, [step.kind]: Math.max(0, counts[step.kind] - step.weight) }
  return Object.values(next).some((n) => n > 0) ? next : null
}

// A failed read must not vanish into "Explored – 3 reads": it ends the group
// and gets its own line.
function failExploreStep(state: State, step: ExploreStep, summary: string): State {
  const remaining = state.exploring ? withoutCount(state.exploring, step) : null
  const flushed = flushExploring({ ...state, exploring: remaining, exploreStep: null })
  const text = `${step.label}: ${summary}`
  return push(flushed, (id) => ({ id, kind: "note", tone: "error", text }))
}

function onActionResult(state: State, event: Extract<CoreEvent, { type: "action_result" }>): State {
  const { inflight, exploreStep } = state
  const result: ActionResult = { ok: event.ok, summary: event.summary, output: event.output, exit_code: event.exit_code, path: event.path, first_line: event.first_line }
  if (inflight && inflight.step === event.step) {
    const done = { ...state, inflight: null }
    return push(done, (id) => ({ id, kind: "tool", step: inflight.step, planStep: inflight.planStep, action: inflight.action, args: inflight.args, result }))
  }
  if (!exploreStep || exploreStep.step !== event.step) return state
  if (event.ok) return { ...state, exploreStep: null }
  return failExploreStep(state, exploreStep, event.summary)
}

function ensureLive(state: State, streamId: string): Live {
  if (state.live && state.live.streamId === streamId) return state.live
  return { streamId, answer: "", reasoning: "", thinking: false, thinkingSince: null }
}

function commitLive(state: State, text: string): State {
  const next = { ...state, live: null }
  if (!text.trim()) return next
  return push(next, (id) => ({ id, kind: "answer", text, done: true }))
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
  return commandNotes(applyEffects(done, effects), event)
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
      const next = { ...state, info }
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
      return push(state, (id) => ({ id, kind: "plan", complexity: event.complexity, steps: event.plan, risks: event.risks }))
    case "route":
      return event.role ? { ...state, info: { ...state.info, routedRole: event.role } } : state
    case "step": {
      const args = event.action_args ?? {}
      const kind = EXPLORE_KIND[event.action_type]
      const seconds = event.reasoning_seconds || 0
      const text = (event.thought ?? "").trim()
      // A step right after a streamed reasoning_commit already narrated its thinking on
      // that stream; its own short `thought` field would only repeat it as a second,
      // near-duplicate card. A routine note on a read-only step would also end its
      // group after every read, so only real thinking time earns a break there.
      const alreadyNarrated = state.justReasoned
      const shown = kind ? seconds > 0 : !alreadyNarrated && (Boolean(text) || seconds > 0)
      const settled = state.inflight ? settle(state) : state
      const withThought = shown ? push(settled, (id) => ({ id, kind: "thought", seconds, text })) : settled
      // The friendly one-liner precedes the action it describes; explore-kind steps skip
      // it, since they are already folded into one "Explored" line.
      const say = kind ? "" : event.user_message.trim()
      const next = say ? push(withThought, (id) => ({ id, kind: "say", text: say })) : withThought
      if (kind) {
        const weight = exploreWeight(kind, args)
        const label = `${event.action_type}  ${actionTarget(args)}`.trim()
        return { ...explore(next, kind, weight, label), exploreStep: { step: event.step, kind, weight, label }, justReasoned: false }
      }
      const inflight: Inflight = { step: event.step, planStep: event.plan_step, action: event.action_type, args, note: event.action_description ?? "" }
      return { ...next, inflight, justReasoned: false }
    }
    case "action_result":
      return onActionResult(state, event)
    case "sandbox_notice":
      return push(state, (id) => ({ id, kind: "note", tone: "info", text: event.note }))
    case "stream_start":
      return { ...state, live: ensureLive(state, event.stream_id) }
    case "stream_chunk":
      return { ...state, live: { ...ensureLive(state, event.stream_id), answer: event.text } }
    case "stream_commit":
      return commitLive(state, event.text)
    case "reasoning_start":
      return { ...state, live: { ...ensureLive(state, event.stream_id), thinking: true, thinkingSince: now } }
    case "reasoning_chunk":
      return { ...state, live: { ...ensureLive(state, event.stream_id), thinking: true, reasoning: event.text } }
    case "reasoning_commit": {
      const current = ensureLive(state, event.stream_id)
      const next = { ...state, live: { ...current, thinking: false, thinkingSince: null, reasoning: "" } }
      const text = current.reasoning.trim()
      const result = event.duration_seconds >= 1 || text
        ? push(next, (id) => ({ id, kind: "thought", seconds: event.duration_seconds, text }))
        : next
      return { ...result, justReasoned: true }
    }
    case "message":
      return push(state, (id) => ({ id, kind: "answer", text: event.markdown, done: true }))
    case "final": {
      // A streamed reply and its `final` carry the same text: show it once.
      if (lastAnswerText(state) === event.message.trim()) return state
      return push(state, (id) => ({ id, kind: "answer", text: event.message, done: true }))
    }
    case "usage": {
      const u = state.usage
      return {
        ...state,
        usage: {
          input: u.input + (event.input_tokens || 0),
          output: u.output + (event.output_tokens || 0),
          // 0 means "this call carried no conversation": keep the last size.
          context: event.context_tokens > 0 ? event.context_tokens : u.context,
          limit: event.context_tokens > 0 && event.context_limit > 0 ? event.context_limit : u.limit,
        },
      }
    }
    case "debug":
      return push(state, (id) => ({ id, kind: "note", tone: "debug", text: event.message }))
    case "error":
      return push(state, (id) => ({ id, kind: "note", tone: "error", text: event.message }))
    case "input_request":
      return { ...state, pending: event }
    case "command_result":
      return onCommandResult(state, event)
    case "turn_end":
      // `command` is cleared too: `resume` is dispatched as a command but ends
      // as a turn, and a core that answers any command this way must not leave
      // the input box disabled forever.
      return { ...settle(state), running: false, live: null, status: null, pending: null, startedAt: null, command: null }
    default:
      return state
  }
}

function noticeText(event: Extract<JournalEvent, { type: "journal_notice" }>): string {
  if (event.omitted_turns) return `${event.omitted_turns} older turns are not shown.`
  if (event.omitted_events) return `${event.omitted_events} events of this turn are not shown.`
  // A notice with no count is the caller saying the journal it was given starts mid-way.
  return "Earlier events are not shown."
}

// A session redrawn from its journal, through the reducer live events use. Token counts are
// skipped: they describe the conversation now running, not the one being shown.
function restore(state: State, events: JournalEvent[], now: number): State {
  let next = reduce(state, { type: "clear" })
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

export function reduce(state: State, action: Action): State {
  switch (action.type) {
    case "event":
      return onEvent(state, action.event, action.now ?? Date.now())
    case "restore":
      return restore(state, action.events, action.now ?? Date.now())
    case "submit":
      return push(
        { ...state, running: true, startedAt: action.now ?? Date.now(), info: { ...state.info, routedRole: undefined } },
        (id) => ({ id, kind: "user", text: action.text }),
      )
    case "command_start":
      return { ...state, command: action.name }
    case "resume_start":
      return { ...state, running: true, startedAt: action.now ?? Date.now() }
    case "note":
      return push(state, (id) => ({ id, kind: "note", tone: action.tone, text: action.text }))
    case "answered":
      return { ...state, pending: null }
    case "core_exit": {
      const next = { ...settle(state), running: false, live: null, status: null, pending: null, command: null }
      if (action.expected) return next
      return push(next, (id) => ({ id, kind: "note", tone: "error", text: `The Kvist core stopped unexpectedly (exit code ${action.code ?? "?"}).` }))
    }
    case "toggle_debug":
      return { ...state, showDebug: !state.showDebug }
    case "clear":
      return { ...state, items: [], exploring: null, exploringNames: [], exploreStep: null, inflight: null, justReasoned: false, epoch: state.epoch + 1 }
  }
}
