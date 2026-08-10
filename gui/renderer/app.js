"use strict"

const { ProtocolState } = window.KvistProtocol

const welcome = document.querySelector("#welcome")
const conversation = document.querySelector("#conversation")
const messages = document.querySelector("#messages")
const composer = document.querySelector("#composer")
const promptInput = document.querySelector("#promptInput")
const workspaceButton = document.querySelector("#workspaceButton")
const workspaceLabel = document.querySelector("#workspaceLabel")
const protocolLabel = document.querySelector("#protocolLabel")
const activityText = document.querySelector("#activityText")
const activitySpinner = document.querySelector("#activitySpinner")
const progressBar = document.querySelector("#progressBar")
const requestHost = document.querySelector("#requestHost")
const paletteBackdrop = document.querySelector("#paletteBackdrop")
const settingsBackdrop = document.querySelector("#settingsBackdrop")
const settingsForm = document.querySelector("#settingsForm")
const settingsClose = document.querySelector("#settingsClose")
const settingsCancel = document.querySelector("#settingsCancel")
const settingsReset = document.querySelector("#settingsReset")
const settingsError = document.querySelector("#settingsError")
const providerSelect = document.querySelector("#providerSelect")
const customProviderInput = document.querySelector("#customProviderInput")
const roleSelect = document.querySelector("#roleSelect")
const approvalModeSelect = document.querySelector("#approvalModeSelect")
const fastModelInput = document.querySelector("#fastModelInput")
const smartModelInput = document.querySelector("#smartModelInput")
const plannerModelInput = document.querySelector("#plannerModelInput")
const roleButton = document.querySelector("#roleButton")
const modelButton = document.querySelector("#modelButton")
const approvalButton = document.querySelector("#approvalButton")

const CONFIG_STORAGE_KEY = "kvist.run-options.v1"
const BUILTIN_PROVIDERS = new Set(["", "openai", "anthropic", "openrouter", "omniroute", "freeflow"])

let workspace = ""
let running = false
let protocol = new ProtocolState()
let showDetails = false
let lastAssistantText = ""
let interruptDeadline = 0
let runtimeConfig = null

function normalizedRunOptions(value = {}) {
  const role = new Set(["auto", "coder", "devops", "chat"]).has(value.role)
    ? value.role
    : "auto"
  const approvalMode = new Set(["ask", "deny"]).has(value.approvalMode)
    ? value.approvalMode
    : "ask"
  return {
    provider: typeof value.provider === "string" ? value.provider.trim() : "",
    role,
    approvalMode,
    models: {
      fast: typeof value.models?.fast === "string" ? value.models.fast.trim() : "",
      smart: typeof value.models?.smart === "string" ? value.models.smart.trim() : "",
      planner: typeof value.models?.planner === "string" ? value.models.planner.trim() : "",
    },
  }
}

function loadRunOptions() {
  try {
    return normalizedRunOptions(JSON.parse(localStorage.getItem(CONFIG_STORAGE_KEY) || "{}"))
  } catch {
    return normalizedRunOptions()
  }
}

let runOptions = loadRunOptions()

function createElement(tag, className, text) {
  const element = document.createElement(tag)
  if (className) element.className = className
  if (text !== undefined) element.textContent = text
  return element
}

function updateConfigChips() {
  const shown = runtimeConfig || runOptions
  const role = shown.role || "auto"
  const approvalMode = shown.approvalMode || shown.approval_mode || "ask"
  const provider = shown.provider || "saved"
  const models = shown.models || {}
  const model = models.smart || models.fast || "models"
  roleButton.textContent = role
  approvalButton.textContent = approvalMode
  modelButton.textContent = `${provider} · ${model}`
  modelButton.title = [
    `Provider: ${provider}`,
    `Fast: ${models.fast || "saved"}`,
    `Smart: ${models.smart || "saved"}`,
    `Planner: ${models.planner || "saved"}`,
  ].join("\n")
}

function shortWorkspace(value) {
  if (!value) return "Choose workspace"
  const normalized = value.replace(/\\/g, "/")
  const parts = normalized.split("/").filter(Boolean)
  if (parts.length <= 3) return value
  return `…/${parts.slice(-3).join("/")}`
}

function updateWorkspace(value) {
  workspace = value
  workspaceLabel.textContent = shortWorkspace(value)
  workspaceButton.title = value
}

function resizePrompt() {
  promptInput.style.height = "auto"
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 190)}px`
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    conversation.scrollTop = conversation.scrollHeight
  })
}

function setRunning(value) {
  running = value
  promptInput.disabled = value || requestHost.childElementCount > 0
  composer.classList.toggle("is-disabled", promptInput.disabled)
  workspaceButton.disabled = value
  roleButton.disabled = value
  modelButton.disabled = value
  approvalButton.disabled = value
  if (!promptInput.disabled) promptInput.focus()
}

function populateSettings() {
  const provider = runOptions.provider
  providerSelect.value = BUILTIN_PROVIDERS.has(provider) ? provider : ""
  customProviderInput.value = BUILTIN_PROVIDERS.has(provider) ? "" : provider
  roleSelect.value = runOptions.role
  approvalModeSelect.value = runOptions.approvalMode
  fastModelInput.value = runOptions.models.fast
  smartModelInput.value = runOptions.models.smart
  plannerModelInput.value = runOptions.models.planner
  settingsError.textContent = ""
}

function showSettings() {
  if (running) return
  hidePalette()
  populateSettings()
  settingsBackdrop.classList.remove("is-hidden")
  providerSelect.focus()
}

function hideSettings() {
  settingsBackdrop.classList.add("is-hidden")
  settingsError.textContent = ""
  if (!promptInput.disabled) promptInput.focus()
}

function saveSettings() {
  const models = {
    fast: fastModelInput.value.trim(),
    smart: smartModelInput.value.trim(),
    planner: plannerModelInput.value.trim(),
  }
  const modelValues = Object.values(models)
  if (modelValues.some(Boolean) && !modelValues.every(Boolean)) {
    settingsError.textContent = "Fill all three model fields, or clear all three."
    return false
  }
  const customProvider = customProviderInput.value.trim()
  if (customProvider && !/^custom:[a-z0-9][a-z0-9_-]{0,63}$/i.test(customProvider)) {
    settingsError.textContent = "Custom provider ID must look like custom:provider-name."
    return false
  }
  runOptions = normalizedRunOptions({
    provider: customProvider || providerSelect.value,
    role: roleSelect.value,
    approvalMode: approvalModeSelect.value,
    models,
  })
  localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(runOptions))
  runtimeConfig = null
  updateConfigChips()
  return true
}

function setActivity(text, { busy = false, progress = null, error = false } = {}) {
  activityText.textContent = text
  activityText.style.color = error ? "var(--error)" : ""
  activitySpinner.classList.toggle("is-hidden", !busy)
  if (progress !== null) {
    const safeProgress = Math.max(0, Math.min(100, Number(progress) || 0))
    progressBar.style.width = `${safeProgress}%`
  }
}

function enterConversation() {
  welcome.classList.add("is-hidden")
  conversation.classList.remove("is-hidden")
}

function addUserMessage(text) {
  const card = createElement("article", "message message-user", text)
  messages.append(card)
  scrollToBottom()
}

function renderMarkdown(target, text) {
  target.innerHTML = window.kvist.renderMarkdown(text)
}

function addAssistantMessage(text, streamId = "") {
  enterConversation()
  const card = createElement("article", "message message-assistant")
  if (streamId) card.dataset.streamId = streamId
  card.append(createElement("span", "assistant-mark", "◆"))
  const content = createElement("div", "markdown")
  renderMarkdown(content, text)
  card.append(content)
  messages.append(card)
  lastAssistantText = text
  scrollToBottom()
  return card
}

function findStreamCard(streamId) {
  return Array.from(messages.querySelectorAll("[data-stream-id]")).find(
    (candidate) => candidate.dataset.streamId === streamId,
  )
}

function updateStream(streamId, text, committed) {
  let card = findStreamCard(streamId)
  if (!card) card = addAssistantMessage("", streamId)
  const content = card.querySelector(".markdown")
  renderMarkdown(content, text)
  card.classList.toggle("is-streaming", !committed)
  lastAssistantText = text
  scrollToBottom()
}

// The thinking block lives inside the answer card (shared stream_id), above the
// answer. Its body deliberately is NOT `.markdown` so updateStream keeps
// targeting the answer element.
function ensureThinkingBlock(streamId) {
  let card = findStreamCard(streamId)
  if (!card) card = addAssistantMessage("", streamId)
  let block = card.querySelector(".thinking-block")
  if (block) return block
  block = createElement("details", "thinking-block")
  block.open = true
  block.dataset.startedAt = String(Date.now())
  const summary = createElement("summary", "thinking-summary")
  summary.append(
    createElement("span", "thinking-spinner"),
    createElement("span", "thinking-label", "Thinking…"),
  )
  block.append(summary, createElement("div", "thinking-body"))
  card.insertBefore(block, card.querySelector(".markdown"))
  return block
}

function updateThinking(streamId, text) {
  const block = ensureThinkingBlock(streamId)
  renderMarkdown(block.querySelector(".thinking-body"), text)
  scrollToBottom()
}

function commitThinkingBlock(streamId, durationSeconds) {
  const card = findStreamCard(streamId)
  const block = card && card.querySelector(".thinking-block")
  if (!block) return
  let seconds = durationSeconds
  if (!seconds || seconds <= 0) {
    const started = Number(block.dataset.startedAt || 0)
    if (started) seconds = (Date.now() - started) / 1000
  }
  block.classList.add("is-committed")
  block.open = false
  block.querySelector(".thinking-spinner")?.remove()
  const label = block.querySelector(".thinking-label")
  if (label) label.textContent = `Thought for ${Math.max(1, Math.round(seconds))}s`
}

function addError(message) {
  enterConversation()
  messages.append(createElement("article", "message message-error", message))
  scrollToBottom()
}

function addNotice(message) {
  enterConversation()
  messages.append(createElement("article", "message message-notice", message))
  scrollToBottom()
}

function addPlan(event) {
  enterConversation()
  const details = createElement("details", "message plan-card")
  details.open = true
  const summary = createElement(
    "summary",
    "",
    `Plan · ${event.complexity || "task"} · ${(event.plan || []).length} steps`,
  )
  const content = createElement("div", "plan-content")
  if (event.goal) content.append(createElement("div", "", event.goal))
  const list = createElement("ol")
  for (const item of event.plan || []) list.append(createElement("li", "", item))
  content.append(list)
  if (event.risks?.length) {
    content.append(createElement("div", "", `Risks: ${event.risks.join(" · ")}`))
  }
  details.append(summary, content)
  messages.append(details)
  scrollToBottom()
}

function addStep(event) {
  enterConversation()
  const details = createElement("details", "message step-card")
  details.open = showDetails
  const summary = createElement("summary")
  summary.append(
    createElement("span", "step-action", event.action_type || event.plan_step || "step"),
    createElement("span", "", event.user_message || `Step ${event.step}`),
  )
  if (event.reasoning_seconds > 0) {
    summary.append(
      createElement("span", "step-thought", `🧠 ${Math.max(1, Math.round(event.reasoning_seconds))}s`),
    )
  }
  summary.append(createElement("span", "step-progress", `${event.progress ?? 0}%`))
  const content = createElement("div", "step-content")
  if (event.thought) content.append(createElement("div", "", event.thought))
  if (event.action_args && Object.keys(event.action_args).length) {
    content.append(createElement("pre", "", JSON.stringify(event.action_args, null, 2)))
  }
  details.append(summary, content)
  messages.append(details)
  scrollToBottom()
}

async function answerRequest(requestId, answer) {
  try {
    const response = protocol.createResponse(requestId, answer)
    await window.kvist.respond(response)
    requestHost.replaceChildren()
    setRunning(true)
    setActivity("Kvist is continuing…", { busy: true })
  } catch (error) {
    addError(error.message)
  }
}

function actionButton(label, className, onClick) {
  const button = createElement("button", className, label)
  button.type = "button"
  button.addEventListener("click", onClick)
  return button
}

function showInputRequest(request) {
  const panel = createElement("section", "request-panel")
  panel.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return
    event.preventDefault()
    event.stopPropagation()
    const cancelled = request.kind === "confirm" ? false : null
    void answerRequest(request.request_id, cancelled)
  })
  const content = createElement("div", "request-content")
  const heading = createElement("div", "request-heading")
  heading.append(
    createElement("span", "request-icon", "△"),
    createElement("span", "", request.title || "Input required"),
  )
  content.append(heading)
  if (request.body) content.append(createElement("div", "request-body", request.body))
  if (request.help_text) content.append(createElement("div", "request-help", request.help_text))

  const actions = createElement("div", "request-actions")
  let initialFocus = null

  if (request.kind === "choice") {
    const options = createElement("div", "request-options")
    for (const [index, option] of (request.options || []).entries()) {
      const button = createElement("button")
      button.type = "button"
      const copy = createElement("span", "option-copy", option.label || option.value)
      if (option.description) copy.append(createElement("small", "", option.description))
      button.append(createElement("span", "option-index", `${index + 1}.`), copy)
      button.addEventListener("click", () => answerRequest(request.request_id, option.value))
      options.append(button)
      if (!initialFocus) initialFocus = button
    }
    content.append(options)
    actions.append(
      actionButton("Cancel", "", () => answerRequest(request.request_id, null)),
    )
  } else if (request.kind === "text" || request.kind === "secret") {
    const field = createElement("input", "request-field")
    field.type = request.kind === "secret" ? "password" : "text"
    field.autocomplete = "off"
    field.value = request.kind === "secret" ? "" : request.current_value || ""
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault()
        void answerRequest(request.request_id, field.value)
      }
    })
    content.append(field)
    actions.append(
      actionButton("Cancel", "", () => answerRequest(request.request_id, null)),
      actionButton("Submit", "allow-button", () => answerRequest(request.request_id, field.value)),
    )
    initialFocus = field
  } else if (request.kind === "confirm") {
    const deny = actionButton("Deny", "deny-button", () => answerRequest(request.request_id, false))
    const allow = actionButton("Allow once", "allow-button", () => answerRequest(request.request_id, true))
    actions.append(deny, allow)
    initialFocus = deny
  } else {
    void answerRequest(request.request_id, null)
    return
  }

  panel.append(content, actions)
  requestHost.replaceChildren(panel)
  promptInput.disabled = true
  composer.classList.add("is-disabled")
  setActivity("Waiting for your input", { busy: false })
  initialFocus?.focus()
}

function handleEvent(event) {
  const action = protocol.consume(event)
  if (action.kind === "protocol_error") {
    addError(action.message)
    setRunning(false)
    setActivity("Protocol error", { error: true })
    return
  }
  if (action.kind === "ignore") return

  if (action.kind === "stream_snapshot") {
    updateStream(action.streamId, action.text, action.committed)
    return
  }

  if (action.kind === "reasoning_start") {
    ensureThinkingBlock(action.streamId)
    return
  }

  if (action.kind === "reasoning_snapshot") {
    if (action.committed) commitThinkingBlock(action.streamId, action.durationSeconds)
    else updateThinking(action.streamId, action.text)
    return
  }

  switch (action.kind) {
    case "hello":
      protocolLabel.textContent = `v${event.protocol_version}`
      runtimeConfig = {
        provider: event.provider || runOptions.provider || "saved",
        models: event.models || runOptions.models,
        role: event.role || runOptions.role,
        approvalMode: event.approval_mode || runOptions.approvalMode,
      }
      updateConfigChips()
      setActivity(`Connected · protocol v${event.protocol_version}`, { busy: true, progress: 2 })
      break
    case "live_start":
      setActivity("Kvist is working…", { busy: true })
      break
    case "live_stop":
      setActivity("Finishing…", { busy: true })
      break
    case "status":
      setActivity(event.user_message || event.status || "Working…", {
        busy: !event.is_error,
        progress: event.progress,
        error: event.is_error,
      })
      break
    case "plan":
      addPlan(event)
      break
    case "step":
      addStep(event)
      break
    case "sandbox_notice":
      addNotice(event.note)
      break
    case "stream_start":
      updateStream(event.stream_id, "", false)
      break
    case "message":
      addAssistantMessage(event.markdown || "")
      break
    case "final":
      if (event.message && event.message !== lastAssistantText) addAssistantMessage(event.message)
      setRunning(false)
      setActivity("Done", { progress: 100 })
      break
    case "debug":
      if (showDetails) addNotice(event.message)
      break
    case "error":
      // An `error` event is terminal (UI_API §8), so the run is over.
      addError(event.message || "Kvist reported an error.")
      setRunning(false)
      setActivity("Kvist reported an error", { error: true })
      break
    case "input_request":
      showInputRequest(event)
      break
  }
}

async function startGoal(goal) {
  const value = String(goal).trim()
  if (!value || running) return
  protocol = new ProtocolState()
  lastAssistantText = ""
  requestHost.replaceChildren()
  enterConversation()
  addUserMessage(value)
  promptInput.value = ""
  resizePrompt()
  setRunning(true)
  setActivity("Starting Kvist…", { busy: true, progress: 0 })
  try {
    await window.kvist.start(value, workspace, runOptions)
  } catch (error) {
    setRunning(false)
    addError(error.message)
    setActivity("Could not start Kvist", { error: true })
  }
}

function resetView() {
  if (running) return
  protocol = new ProtocolState()
  lastAssistantText = ""
  messages.replaceChildren()
  requestHost.replaceChildren()
  conversation.classList.add("is-hidden")
  welcome.classList.remove("is-hidden")
  progressBar.style.width = "0%"
  setActivity("Ready")
  promptInput.value = ""
  resizePrompt()
  promptInput.focus()
}

async function interrupt() {
  if (!running) return
  const now = Date.now()
  if (now > interruptDeadline) {
    interruptDeadline = now + 1500
    setActivity("Press Escape again to interrupt", { busy: true })
    return
  }
  interruptDeadline = 0
  setActivity("Interrupting…", { busy: true })
  await window.kvist.stop()
  requestHost.replaceChildren()
  setRunning(false)
  setActivity("Interrupted", { error: true })
}

function showPalette() {
  paletteBackdrop.classList.remove("is-hidden")
  paletteBackdrop.querySelector("button")?.focus()
}

function hidePalette() {
  paletteBackdrop.classList.add("is-hidden")
  if (!promptInput.disabled) promptInput.focus()
}

function toggleDetails() {
  showDetails = !showDetails
  for (const item of document.querySelectorAll(".step-card")) item.open = showDetails
  setActivity(showDetails ? "Tool details shown" : "Tool details hidden")
}

composer.addEventListener("submit", (event) => {
  event.preventDefault()
  void startGoal(promptInput.value)
})

promptInput.addEventListener("input", resizePrompt)
promptInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return
  if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return
  event.preventDefault()
  composer.requestSubmit()
})

workspaceButton.addEventListener("click", async () => {
  if (running) return
  const selected = await window.kvist.chooseWorkspace()
  if (selected) updateWorkspace(selected)
})

for (const button of [roleButton, modelButton, approvalButton]) {
  button.addEventListener("click", showSettings)
}

settingsBackdrop.addEventListener("click", (event) => {
  if (event.target === settingsBackdrop) hideSettings()
})

settingsBackdrop.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return
  event.preventDefault()
  event.stopPropagation()
  hideSettings()
})

settingsClose.addEventListener("click", hideSettings)
settingsCancel.addEventListener("click", hideSettings)
settingsReset.addEventListener("click", () => {
  runOptions = normalizedRunOptions()
  runtimeConfig = null
  localStorage.removeItem(CONFIG_STORAGE_KEY)
  populateSettings()
  updateConfigChips()
})

settingsForm.addEventListener("submit", (event) => {
  event.preventDefault()
  if (saveSettings()) hideSettings()
})

paletteBackdrop.addEventListener("click", (event) => {
  if (event.target === paletteBackdrop) hidePalette()
})

paletteBackdrop.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return
  event.preventDefault()
  event.stopPropagation()
  hidePalette()
})

for (const button of paletteBackdrop.querySelectorAll("[data-command]")) {
  button.addEventListener("click", async () => {
    const command = button.dataset.command
    hidePalette()
    if (command === "new") resetView()
    if (command === "workspace" && !running) {
      const selected = await window.kvist.chooseWorkspace()
      if (selected) updateWorkspace(selected)
    }
    if (command === "settings") showSettings()
    if (command === "details") toggleDetails()
    if (command === "interrupt") await interrupt()
  })
}

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") {
    event.preventDefault()
    if (paletteBackdrop.classList.contains("is-hidden")) showPalette()
    else hidePalette()
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") {
    event.preventDefault()
    resetView()
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "m") {
    event.preventDefault()
    showSettings()
  }
  if (
    event.key === "Escape"
    && paletteBackdrop.classList.contains("is-hidden")
    && settingsBackdrop.classList.contains("is-hidden")
  ) {
    void interrupt()
  }
})

window.kvist.onEvent(handleEvent)
window.kvist.onProtocolError(({ message }) => {
  addError(message)
  setRunning(false)
  setActivity("Protocol error", { error: true })
})
window.kvist.onProcessError(({ message }) => {
  addError(message)
  setRunning(false)
  setActivity("Kvist process failed", { error: true })
})
window.kvist.onExit(({ expected, code }) => {
  // One process = one run: a clean exit (code 0) is normal completion, even
  // when the run ended without a `final` event (chat replies and streamed
  // reports do not emit one). Quietly return to idle. Abnormal exits are
  // surfaced separately via process-error, and a run that already finished
  // (final/error → not running) keeps its own status.
  if (expected || code !== 0 || !running) return
  setRunning(false)
  setActivity("Kvist", { progress: 0 })
})

updateConfigChips()

window.kvist.bootstrap().then((state) => {
  updateWorkspace(state.workspace)
  protocolLabel.textContent = `v${state.protocolVersion}`
  promptInput.focus()
  if (state.initialGoal) void startGoal(state.initialGoal)
})
