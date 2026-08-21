"use strict"

const { ProtocolState } = window.KvistProtocol

const welcome = document.querySelector("#welcome")
const conversation = document.querySelector("#conversation")
const messages = document.querySelector("#messages")
const sidebar = document.querySelector("#sidebar")
const newChatButton = document.querySelector("#newChatButton")
const addProjectButton = document.querySelector("#addProjectButton")
const projectList = document.querySelector("#projectList")
const recentList = document.querySelector("#recentList")
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
const tokenSpeedLabel = document.querySelector("#tokenSpeed")
const metricsBar = document.querySelector("#metricsBar")
const metricTokens = document.querySelector("#metricTokens")
const metricSteps = document.querySelector("#metricSteps")
const metricThinking = document.querySelector("#metricThinking")
const metricElapsed = document.querySelector("#metricElapsed")

const CONFIG_STORAGE_KEY = "kvist.run-options.v1"
const BUILTIN_PROVIDERS = new Set(["", "openai", "anthropic", "openrouter", "omniroute", "freeflow"])

let workspace = ""
let running = false
let protocol = new ProtocolState()
let showDetails = false
let lastAssistantText = ""
let interruptDeadline = 0
let runtimeConfig = null
let navigationProjects = []
let activeSessionName = ""
const expandedProjects = new Set()

// Live output-speed indicator. The protocol carries cumulative text snapshots
// (no token counts), so the rate is estimated from text growth: chars ÷ 4, the
// same fallback the core uses in count_tokens. Snapshots arrive at the core's
// ~10 FPS render throttle, which preserves the true average output rate.
const TOKEN_RATE_WINDOW_MS = 2000
const CHARS_PER_TOKEN = 4
let tokenSamples = []
const streamTextLengths = new Map()
let tokenSpeedTimer = 0

// Run metrics for the top bar. Token totals come from the core's `usage`
// events (real tiktoken counts per model call); while a response is still
// streaming, the not-yet-committed text is added as a live estimate.
let runStats = freshRunStats()

function freshRunStats() {
  return {
    tokensIn: 0,
    tokensOut: 0,
    steps: 0,
    reasoningSeconds: 0,
    startedAt: 0,
    frozenAt: 0,
  }
}

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

function projectName(value) {
  const normalized = String(value || "").replace(/\\/g, "/").replace(/\/$/, "")
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "Project"
}

function folderIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg")
  svg.classList.add("folder-icon")
  svg.setAttribute("viewBox", "0 0 24 24")
  svg.setAttribute("aria-hidden", "true")
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path")
  path.setAttribute("d", "M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z")
  svg.append(path)
  return svg
}

function sessionItem(project, name) {
  return (project?.status?.session_items || []).find((item) => item.name === name) || null
}

function currentProject() {
  return navigationProjects.find((project) => project.path === workspace) || null
}

function displaySession(item) {
  resetView()
  if (!item) return
  activeSessionName = item.name
  const chatHistory = (item.chat_history || []).filter(
    (message) => message?.role === "user" || message?.role === "assistant",
  )
  const hasUserChat = chatHistory.some((message) => message.role === "user")
  const sessionHistory = Array.isArray(item.session_history) ? item.session_history : []
  if (!hasUserChat && !sessionHistory.length) {
    setActivity(item.has_checkpoint ? "Unfinished task" : "Ready")
    return
  }

  enterConversation()
  if (hasUserChat) {
    for (const message of chatHistory) {
      const content = typeof message.content === "string" ? message.content : ""
      if (!content) continue
      if (message.role === "user") addUserMessage(content)
      else addAssistantMessage(content)
    }
  } else {
    for (const entry of sessionHistory) {
      if (!entry || typeof entry !== "object") continue
      if (typeof entry.goal === "string" && entry.goal) addUserMessage(entry.goal)
      if (typeof entry.result === "string" && entry.result) addAssistantMessage(entry.result)
    }
  }
  setActivity(item.has_checkpoint ? "Unfinished task" : "Ready")
}

function renderNavigation() {
  projectList.replaceChildren()
  recentList.replaceChildren()

  if (!navigationProjects.length) {
    projectList.append(createElement("div", "empty-navigation", "No projects yet"))
  }

  for (const project of navigationProjects) {
    const group = createElement("div", "project-group")
    const row = createElement("button", "project-row")
    row.type = "button"
    row.title = project.path
    row.disabled = !project.available
    const expanded = expandedProjects.has(project.path)
    row.setAttribute("aria-expanded", String(expanded))
    if (project.path === workspace) row.classList.add("is-active")
    row.append(
      createElement("span", "project-chevron", "›"),
      folderIcon(),
      createElement("span", "navigation-label", projectName(project.path)),
      createElement(
        "span",
        `project-status-dot${project.available ? "" : " is-unavailable"}`,
      ),
    )
    row.addEventListener("click", () => void toggleProject(project))
    group.append(row)

    if (expanded) {
      const sessions = createElement("div", "project-sessions")
      const items = project.status?.session_items || []
      if (!items.length) {
        sessions.append(createElement("div", "empty-navigation", "No sessions"))
      }
      for (const item of items) {
        const button = createElement("button", "session-row")
        button.type = "button"
        button.title = `${item.title}\n${item.name}`
        button.append(createElement("span", "navigation-label", item.title))
        if (project.path === workspace && item.name === activeSessionName) {
          button.classList.add("is-active")
        }
        button.addEventListener("click", (event) => {
          event.stopPropagation()
          void selectSession(project.path, item.name)
        })
        sessions.append(button)
      }
      group.append(sessions)
    }
    projectList.append(group)
  }

  const recents = navigationProjects
    .flatMap((project) => (project.status?.session_items || []).map((item) => ({ project, item })))
    .sort((left, right) => Number(right.item.updated_at) - Number(left.item.updated_at))
    .slice(0, 14)
  if (!recents.length) {
    recentList.append(createElement("div", "empty-navigation", "No recent sessions"))
  }
  for (const { project, item } of recents) {
    const button = createElement("button", "recent-row")
    button.type = "button"
    button.title = `${projectName(project.path)} · ${item.title}`
    button.append(createElement("span", "navigation-label", item.title))
    if (project.path === workspace && item.name === activeSessionName) {
      button.classList.add("is-active")
    }
    button.addEventListener("click", () => void selectSession(project.path, item.name))
    recentList.append(button)
  }
}

function updateNavigation(state) {
  navigationProjects = Array.isArray(state?.projects) ? state.projects : []
  updateWorkspace(state?.workspace || workspace)
  const project = currentProject()
  activeSessionName = project?.status?.active_session || ""
  if (workspace) expandedProjects.add(workspace)
  renderNavigation()
}

async function refreshNavigation() {
  try {
    updateNavigation(await window.kvist.navigation())
  } catch (error) {
    setActivity(error.message, { error: true })
  }
}

async function toggleProject(project) {
  if (running || !project.available) return
  if (project.path === workspace) {
    if (expandedProjects.has(project.path)) expandedProjects.delete(project.path)
    else expandedProjects.add(project.path)
    renderNavigation()
    return
  }
  try {
    expandedProjects.add(project.path)
    const state = await window.kvist.activateProject(project.path)
    updateNavigation(state)
    const selected = currentProject()
    displaySession(sessionItem(selected, selected?.status?.active_session))
    renderNavigation()
  } catch (error) {
    addError(error.message)
  }
}

async function selectSession(projectPath, name) {
  if (running) return
  try {
    expandedProjects.add(projectPath)
    const result = await window.kvist.switchSession(projectPath, name)
    updateNavigation(result.navigation)
    const project = currentProject()
    displaySession(sessionItem(project, name))
    renderNavigation()
  } catch (error) {
    addError(error.message)
  }
}

async function createNewSession() {
  if (running || !workspace) return
  try {
    const result = await window.kvist.newSession(workspace)
    expandedProjects.add(workspace)
    updateNavigation(result.navigation)
    resetView()
    activeSessionName = result.name
    renderNavigation()
  } catch (error) {
    addError(error.message)
  }
}

async function chooseProject() {
  if (running) return
  try {
    const selected = await window.kvist.chooseWorkspace()
    if (!selected) return
    expandedProjects.add(selected)
    await refreshNavigation()
    const project = currentProject()
    displaySession(sessionItem(project, project?.status?.active_session))
    renderNavigation()
  } catch (error) {
    addError(error.message)
  }
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

function noteStreamTokens(streamId, text, committed) {
  const previous = streamTextLengths.get(streamId) || 0
  if (committed) streamTextLengths.delete(streamId)
  else streamTextLengths.set(streamId, text.length)
  const added = text.length - previous
  if (added <= 0) return
  tokenSamples.push({ time: performance.now(), tokens: added / CHARS_PER_TOKEN })
  renderTokenSpeed()
}

function renderTokenSpeed() {
  const now = performance.now()
  const cutoff = now - TOKEN_RATE_WINDOW_MS
  tokenSamples = tokenSamples.filter((sample) => sample.time >= cutoff)
  if (!running || tokenSamples.length === 0) {
    tokenSpeedLabel.classList.add("is-hidden")
    return
  }
  const tokens = tokenSamples.reduce((sum, sample) => sum + sample.tokens, 0)
  const spanSeconds = Math.max(0.25, (now - tokenSamples[0].time) / 1000)
  tokenSpeedLabel.textContent = `${Math.round(tokens / spanSeconds)} tok/s`
  tokenSpeedLabel.classList.remove("is-hidden")
}

function resetTokenSpeed() {
  if (tokenSpeedTimer) window.clearInterval(tokenSpeedTimer)
  tokenSpeedTimer = 0
  tokenSamples = []
  streamTextLengths.clear()
  tokenSpeedLabel.classList.add("is-hidden")
}

function formatTokens(value) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return String(Math.round(value))
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  return `${minutes}:${String(rest).padStart(2, "0")}`
}

function renderRunStats() {
  // Text still streaming has no usage event yet; add it as a live estimate.
  let pending = 0
  for (const length of streamTextLengths.values()) pending += length / CHARS_PER_TOKEN
  const tokensIn = runStats.tokensIn
  const tokensOut = runStats.tokensOut + pending
  metricTokens.textContent = `↓ ${formatTokens(tokensIn)} in · ↑ ${formatTokens(tokensOut)} out`
  metricTokens.title =
    `Tokens consumed this run (core tiktoken counts): ${Math.round(tokensIn)} in, ` +
    `${Math.round(runStats.tokensOut)} out${pending >= 1 ? ` (+~${Math.round(pending)} streaming)` : ""}`

  metricSteps.classList.toggle("is-hidden", runStats.steps === 0)
  if (runStats.steps > 0) {
    metricSteps.textContent = `${runStats.steps} step${runStats.steps === 1 ? "" : "s"}`
  }

  metricThinking.classList.toggle("is-hidden", runStats.reasoningSeconds < 0.5)
  if (runStats.reasoningSeconds >= 0.5) {
    metricThinking.textContent = `🧠 ${Math.round(runStats.reasoningSeconds)}s`
  }

  if (runStats.startedAt) {
    const end = runStats.frozenAt || Date.now()
    metricElapsed.textContent = formatElapsed((end - runStats.startedAt) / 1000)
  }
}

function resetRunStats() {
  runStats = freshRunStats()
  renderRunStats()
}

function setRunning(value) {
  running = value
  promptInput.disabled = value || requestHost.childElementCount > 0
  composer.classList.toggle("is-disabled", promptInput.disabled)
  workspaceButton.disabled = value
  roleButton.disabled = value
  modelButton.disabled = value
  approvalButton.disabled = value
  sidebar.classList.toggle("is-disabled", value)
  if (value) {
    if (!tokenSpeedTimer) {
      tokenSpeedTimer = window.setInterval(() => {
        renderTokenSpeed()
        renderRunStats()
      }, 500)
    }
  } else {
    resetTokenSpeed()
    if (runStats.startedAt && !runStats.frozenAt) runStats.frozenAt = Date.now()
    renderRunStats()
  }
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
  metricsBar.classList.remove("is-hidden")
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

// Stream snapshots contain the complete reply.  Re-rendering all of it is
// useful for Markdown, but made every newly received token appear abruptly.
// Keep the completed portion visually stable and animate only the visible
// suffix added by this snapshot.
function animateStreamSuffix(target) {
  const previousText = target.dataset.renderedText || ""
  const renderedText = target.textContent || ""
  target.dataset.renderedText = renderedText

  if (!renderedText || !renderedText.startsWith(previousText)) return

  let offset = 0
  const textNodes = []
  const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) textNodes.push(walker.currentNode)

  for (const node of textNodes) {
    const start = offset
    const end = start + node.data.length
    offset = end
    if (end <= previousText.length) continue

    const suffixStart = Math.max(0, previousText.length - start)
    const fragment = document.createDocumentFragment()
    if (suffixStart) fragment.append(document.createTextNode(node.data.slice(0, suffixStart)))
    const suffix = createElement("span", "stream-token")
    suffix.textContent = node.data.slice(suffixStart)
    fragment.append(suffix)
    node.replaceWith(fragment)
  }
}

function renderStreamingMarkdown(target, text) {
  renderMarkdown(target, text)
  animateStreamSuffix(target)
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
  renderStreamingMarkdown(content, text)
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
  renderStreamingMarkdown(block.querySelector(".thinking-body"), text)
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
    noteStreamTokens(action.streamId, action.text, action.committed)
    updateStream(action.streamId, action.text, action.committed)
    return
  }

  if (action.kind === "reasoning_start") {
    ensureThinkingBlock(action.streamId)
    return
  }

  if (action.kind === "reasoning_snapshot") {
    // Reasoning shares the answer's stream_id, so track it as its own channel:
    // thinking text and answer text are separate cumulative sequences.
    noteStreamTokens(`${action.streamId}:reasoning`, action.text, action.committed)
    if (action.committed) {
      if (action.durationSeconds > 0) {
        runStats.reasoningSeconds += action.durationSeconds
        renderRunStats()
      }
      commitThinkingBlock(action.streamId, action.durationSeconds)
    } else {
      updateThinking(action.streamId, action.text)
    }
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
      runStats.steps = Math.max(runStats.steps, Number(event.step) || 0)
      runStats.reasoningSeconds += Number(event.reasoning_seconds) || 0
      renderRunStats()
      addStep(event)
      break
    case "usage":
      runStats.tokensIn += Number(event.input_tokens) || 0
      runStats.tokensOut += Number(event.output_tokens) || 0
      renderRunStats()
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
      void refreshNavigation()
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
  resetTokenSpeed()
  resetRunStats()
  runStats.startedAt = Date.now()
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
  resetTokenSpeed()
  resetRunStats()
  messages.replaceChildren()
  requestHost.replaceChildren()
  metricsBar.classList.add("is-hidden")
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

newChatButton.addEventListener("click", () => void createNewSession())
addProjectButton.addEventListener("click", () => void chooseProject())
workspaceButton.addEventListener("click", () => void chooseProject())

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
    if (command === "new") await createNewSession()
    if (command === "workspace" && !running) {
      await chooseProject()
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
    void createNewSession()
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
  void refreshNavigation()
})

updateConfigChips()

window.kvist.bootstrap().then((state) => {
  updateNavigation(state)
  protocolLabel.textContent = `v${state.protocolVersion}`
  const project = currentProject()
  displaySession(sessionItem(project, project?.status?.active_session))
  renderNavigation()
  promptInput.focus()
  if (state.initialGoal) void startGoal(state.initialGoal)
})
