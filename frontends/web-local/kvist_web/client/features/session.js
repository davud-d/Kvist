import { api } from "/assets/utils/api.js"
import { state, sessionOf, update } from "/assets/state/store.js"

const conversation = document.querySelector("#conversation"), empty = document.querySelector("#empty-state")
const composer = document.querySelector("#composer"), pending = document.querySelector("#pending-input")
const title = document.querySelector("#session-title"), path = document.querySelector("#session-path")
const agentState = document.querySelector("#agent-state"), stop = document.querySelector("#stop-agent")
const streamNodes = new Map()
let selectionGeneration = 0
let activeRecovery = null

function sameSelection(left, right) {
  return Boolean(left && right && left.serverId === right.serverId && left.workspace === right.workspace && left.session === right.session)
}

export function selectionSnapshot() {
  return state.selected ? { selection: { ...state.selected }, generation: selectionGeneration } : null
}

export function isCurrentSelection(snapshot) {
  return Boolean(snapshot && snapshot.generation === selectionGeneration && sameSelection(snapshot.selection, state.selected))
}

function currentSelectionMatches(selection, generation) {
  return generation === selectionGeneration && sameSelection(selection, state.selected)
}

function message(role, text, cls = "") {
  const row = document.createElement("article"); row.className = `message ${cls}`
  const label = document.createElement("div"); label.className = "message-role"; label.textContent = role
  const body = document.createElement("div"); body.className = "message-body"; body.textContent = text
  row.append(label, body); conversation.append(row); return body
}

function tool(event) {
  const details = document.createElement("details"); details.className = "tool"
  const summary = document.createElement("summary")
  summary.textContent = event.action_description || event.summary || event.action_type || "Tool activity"
  const pre = document.createElement("pre"); pre.textContent = event.output || JSON.stringify(event.action_args || {}, null, 2)
  details.append(summary, pre); conversation.append(details)
}

function rememberEvent(event) {
  // A compliant producer never includes a secret's existing value, but keep
  // browser state safe if an older/third-party producer gets that wrong.
  const value = event.type === "input_request" && event.kind === "secret" ? { ...event, current_value: "" } : event
  state.events.push(value)
  if (state.events.length > 3000) state.events.splice(0, state.events.length - 3000)
}

function reconcileAgentState(event) {
  const kind = event.type
  if (kind === "agent.state_changed" && typeof event.state === "string") {
    setAgentState(event.state)
  } else if (["status", "step", "live_start", "stream_start", "stream_chunk"].includes(kind)) {
    setAgentState("running")
  } else if (kind === "input_request") {
    setAgentState("waiting_for_user")
  } else if (kind === "turn_end") {
    pending.hidden = true
    setAgentState(event.stopped_by_user ? "stopped" : "finished")
  } else if (kind === "error") {
    pending.hidden = true
    setAgentState("failed")
  } else if (kind === "agent.process_exited") {
    pending.hidden = true
    setAgentState(Number(event.code) === 0 ? "finished" : "failed")
  }
}

export function applyEvent(event, { initial = false } = {}) {
  if (!event || typeof event.type !== "string") return
  const kind = event.type
  const knownStream = kind === "stream_start" && typeof event.stream_id === "string" && streamNodes.has(event.stream_id)
  if (!knownStream) rememberEvent(event)
  if (kind === "user") message("you", event.text || "")
  else if (kind === "message") message("kvist", event.markdown || "")
  else if (kind === "final") message("kvist", event.message || "")
  else if (kind === "stream_start" && !knownStream) {
    const body = message("kvist", "")
    if (typeof event.stream_id === "string") streamNodes.set(event.stream_id, body)
  } else if (kind === "stream_chunk") {
    const body = streamNodes.get(event.stream_id); if (body) body.textContent = event.text || ""
  } else if (kind === "stream_commit") {
    const body = streamNodes.get(event.stream_id); if (body) body.textContent = event.text || ""; else message("kvist", event.text || "")
  } else if (kind === "step" || kind === "action_result") tool(event)
  else if (kind === "error") message("error", event.message || "Kvist failed", "error")
  else if (kind === "input_request") showPending(event)
  if (!initial) reconcileAgentState(event)
  if (!initial) conversation.scrollTop = conversation.scrollHeight
  renderInspector()
}

export async function openSession(serverId, workspace, session) {
  const generation = ++selectionGeneration
  update(s => { s.selected = { serverId, workspace, session }; s.events = []; s.eventSeq = 0; s.diffReport = "" })
  streamNodes.clear(); conversation.replaceChildren(); empty.hidden = true; conversation.hidden = false; composer.hidden = false; pending.hidden = true
  const item = sessionOf(); title.textContent = item?.title || session; path.textContent = `${workspace} · ${serverId}`
  setAgentState(item?.execution || "idle")
  const selection = { serverId, workspace, session }
  try {
    await replayDurable(selection, generation, { initial: true })
  } catch (error) {
    if (!currentSelectionMatches(selection, generation)) return false
    throw error
  }
  if (!currentSelectionMatches(selection, generation)) return false
  conversation.scrollTop = conversation.scrollHeight
  renderInspector()
  scheduleRead()
  return true
}

export async function recoverSelected() {
  const snapshot = selectionSnapshot()
  if (!snapshot) return
  const { selection, generation } = snapshot
  const key = `${selection.serverId}\u0000${selection.workspace}\u0000${selection.session}`
  if (activeRecovery?.key === key) {
    // A live durable envelope can arrive after the in-flight history snapshot
    // was taken. Drain once more before settling so its journal cursor cannot
    // be stranded behind the event that woke us.
    activeRecovery.dirty = true
    return activeRecovery.promise
  }
  const recovery = { key, dirty: false, promise: null }
  const promise = (async () => {
    let recovered = 0
    do {
      recovery.dirty = false
      recovered += await replayDurable(selection, generation)
    } while (recovery.dirty && currentSelectionMatches(selection, generation))
    if (recovered && currentSelectionMatches(selection, generation)) scheduleRead()
    return recovered
  })().finally(() => {
    if (activeRecovery?.promise === promise) activeRecovery = null
  })
  recovery.promise = promise
  activeRecovery = recovery
  return promise
}

async function replayDurable(selection, generation, options = {}) {
  let cursor = state.eventSeq, count = 0
  while (true) {
    const history = await api.history(selection, cursor, 1000)
    if (!currentSelectionMatches(selection, generation)) return count
    for (const record of history.events || []) {
      if (record.seq <= state.eventSeq) continue
      state.eventSeq = record.seq
      cursor = record.seq
      count += 1
      applyEvent(record.event, options)
    }
    if (!history.has_more || !(history.events || []).length) return count
  }
}

export function applyLive(envelope) {
  if (!state.selected || !envelope || !envelope.event) return
  const selected = state.selected
  if (envelope.server_id !== selected.serverId || envelope.workspace !== selected.workspace || envelope.session !== selected.session) return
  const event = envelope.event
  const durable = envelope.durable === true
  // stream_start reserves one DOM row so transient cumulative chunks can be
  // painted immediately. Its later durable replay is idempotent by stream_id.
  if (!durable || event.type === "stream_start") applyEvent(event)
  else reconcileAgentState(event)
  if (durable) recoverSelected().catch(() => {})
}

export function setAgentState(value) {
  const stateValue = typeof value === "string" && value ? value : "idle"
  agentState.textContent = stateValue.replaceAll("_", " ")
  stop.hidden = !["running", "waiting_for_user", "starting"].includes(stateValue)
}

function appendSubject(subject) {
  if (!subject || typeof subject !== "object") return
  const details = document.createElement("details"); details.className = "tool"; details.open = true
  const summary = document.createElement("summary")
  const pre = document.createElement("pre")
  if (subject.kind === "command") {
    summary.textContent = "Command awaiting approval"
    pre.textContent = [subject.command, subject.reason].filter(Boolean).join("\n\n") || "No command details were supplied."
  } else if (subject.kind === "overwrite") {
    summary.textContent = subject.path ? `Overwrite ${subject.path}` : "File overwrite awaiting approval"
    pre.textContent = subject.diff || "No diff details were supplied."
  } else {
    summary.textContent = "Request details"
    pre.textContent = JSON.stringify(subject, null, 2)
  }
  details.append(summary, pre); pending.append(details)
}

function pendingField(event) {
  if (event.kind === "choice" && Array.isArray(event.options) && event.options.length) {
    const select = document.createElement("select")
    select.setAttribute("aria-label", event.title || "Choose a response")
    for (const option of event.options) {
      const node = document.createElement("option")
      const value = typeof option === "object" && option ? option.value : option
      const label = typeof option === "object" && option ? option.label || option.value : option
      node.value = String(value ?? ""); node.textContent = String(label ?? value ?? "")
      if (String(value ?? "") === String(event.current_value || "")) node.selected = true
      select.append(node)
    }
    return select
  }
  const input = document.createElement("input")
  input.type = event.kind === "secret" ? "password" : "text"
  input.autocomplete = "off"
  input.value = event.kind === "secret" ? "" : event.current_value || ""
  input.setAttribute("aria-label", event.title || "Response")
  return input
}

function showPending(event) {
  pending.hidden = false; pending.replaceChildren()
  const heading = document.createElement("h3"); heading.textContent = event.title || "Kvist needs input"
  const body = document.createElement("p"); body.textContent = event.body || event.help_text || "Review this request before continuing."
  pending.append(heading, body)
  appendSubject(event.subject)
  if (event.kind === "confirm") {
    const deny = document.createElement("button"); deny.className = "stop-button"; deny.textContent = "Deny"
    const allow = document.createElement("button"); allow.className = "primary-button"; allow.textContent = "Allow"
    let remember = null
    if (event.remember_pattern) {
      const label = document.createElement("label")
      remember = document.createElement("input"); remember.type = "checkbox"
      label.append(remember, document.createTextNode(" Remember this safe approval"))
      pending.append(label)
    }
    deny.onclick = () => respond(event, { approved: false })
    allow.onclick = () => respond(event, { approved: true, ...(remember?.checked ? { remember: true } : {}) })
    pending.append(deny, allow)
    queueMicrotask(() => deny.focus())
    return
  }
  const input = pendingField(event)
  const send = document.createElement("button"); send.className = "primary-button"; send.textContent = "Continue"
  const submit = () => respond(event, { value: input.value })
  send.onclick = submit
  input.addEventListener("keydown", key => { if (key.key === "Enter") { key.preventDefault(); submit() } })
  pending.append(input, send)
  queueMicrotask(() => input.focus())
}

async function respond(event, response) {
  const snapshot = selectionSnapshot()
  if (!snapshot) return
  await api.respond(snapshot.selection, { request_id: event.request_id, ...response })
  if (!isCurrentSelection(snapshot)) return
  pending.hidden = true
  setAgentState("running")
}

let readTimer
function scheduleRead() {
  const snapshot = selectionSnapshot()
  if (!snapshot || state.eventSeq < 1) return
  const cursor = state.eventSeq
  clearTimeout(readTimer)
  readTimer = setTimeout(async () => {
    if (!isCurrentSelection(snapshot) || document.hidden) return
    try {
      await api.markRead(snapshot.selection, cursor)
      if (isCurrentSelection(snapshot)) document.dispatchEvent(new CustomEvent("kvist:navigation-refresh"))
    } catch (_) {
      // A failed read receipt is harmless: a later durable recovery retries it.
    }
  }, 1400)
}

export function renderInspector() {
  const target = document.querySelector("#inspector-content"); target.replaceChildren()
  const tab = state.inspectorTab
  if (tab === "activity") for (const event of state.events.filter(e => ["step", "action_result", "error", "route", "turn_end", "input_request", "agent.process_exited"].includes(e.type)).slice(-100).reverse()) {
    const row = document.createElement("div"); row.className = "timeline-item"; row.textContent = event.summary || event.user_message || event.action_description || event.message || event.title || event.type
    const small = document.createElement("small"); small.textContent = event.action_type || event.plan_step || event.type; row.append(small); target.append(row)
  } else if (tab === "files") {
    const files = [...new Set(state.events.map(e => e.path).filter(Boolean))]
    for (const file of files) { const row = document.createElement("div"); row.className = "file-row"; row.textContent = file; target.append(row) }
    if (!files.length) target.textContent = "No files are associated with this session yet."
  } else if (tab === "diff") {
    const pre = document.createElement("pre"); pre.className = "terminal-view diff-view"; pre.textContent = state.diffReport || "No diff is available yet."; target.append(pre)
  } else if (tab === "terminal") {
    const pre = document.createElement("pre"); pre.className = "terminal-view"; pre.textContent = state.events.filter(e => e.action_type === "run_shell").map(e => e.output || e.summary).join("\n\n") || "No command output in this session."; target.append(pre)
  } else {
    const usage = state.events.filter(e => e.type === "usage").at(-1) || {}
    const values = tab === "context" ? [["Context", usage.context_tokens || 0],["Limit",usage.context_limit || "Unknown"],["Model",usage.model || "—"]] : [["Input tokens",usage.input_tokens || 0],["Output tokens",usage.output_tokens || 0],["Cost","Not reported"]]
    for (const [name,value] of values) { const row = document.createElement("div"); row.className = "metric"; const a=document.createElement("span"),b=document.createElement("span");a.textContent=name;b.textContent=String(value);row.append(a,b);target.append(row) }
  }
}

export async function loadDiff() {
  const snapshot = selectionSnapshot()
  if (!snapshot) return
  const result = await api.changes(snapshot.selection)
  if (!isCurrentSelection(snapshot)) return
  state.diffReport = result.report || "No diff is available yet."
  if (state.inspectorTab === "diff") renderInspector()
}
