"use strict"

const { ProtocolState } = window.KvistProtocol

const welcome = document.querySelector("#welcome")
const sessionSplash = document.querySelector("#sessionSplash")
const sessionSplashLogo = sessionSplash.querySelector("img")
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
const globalSettingsBackdrop = document.querySelector("#globalSettingsBackdrop")
const globalSettingsClose = document.querySelector("#globalSettingsClose")
const settingsGearButton = document.querySelector("#settingsGearButton")
const providerSelect = document.querySelector("#providerSelect")
const customProviderInput = document.querySelector("#customProviderInput")
const roleSelect = document.querySelector("#roleSelect")
const approvalModeSelect = document.querySelector("#approvalModeSelect")
const fastModelInput = document.querySelector("#fastModelInput")
const smartModelInput = document.querySelector("#smartModelInput")
const plannerModelInput = document.querySelector("#plannerModelInput")
const customProviderField = document.querySelector("#customProviderField")
const providerStatus = document.querySelector("#providerStatus")
const providerDetail = document.querySelector("#providerDetail")
const addCustomProviderButton = document.querySelector("#addCustomProviderButton")
const customProviderBackdrop = document.querySelector("#customProviderBackdrop")
const customProviderForm = document.querySelector("#customProviderForm")
const customProviderClose = document.querySelector("#customProviderClose")
const customProviderCancel = document.querySelector("#customProviderCancel")
const customProviderError = document.querySelector("#customProviderError")
const cpConfirmView = document.querySelector("#cpConfirmView")
const cpFormView = document.querySelector("#cpFormView")
const cpConfirmCancel = document.querySelector("#cpConfirmCancel")
const cpConfirmContinue = document.querySelector("#cpConfirmContinue")
const cpFields = {
  name: document.querySelector("#cpName"),
  label: document.querySelector("#cpLabel"),
  protocol: document.querySelector("#cpProtocol"),
  apiKey: document.querySelector("#cpApiKey"),
  baseUrl: document.querySelector("#cpBaseUrl"),
  addV1: document.querySelector("#cpAddV1"),
  addV1Field: document.querySelector("#cpAddV1Field"),
  streaming: document.querySelector("#cpStreaming"),
  oneModel: document.querySelector("#cpOneModel"),
  fast: document.querySelector("#cpModelFast"),
  smart: document.querySelector("#cpModelSmart"),
  planner: document.querySelector("#cpModelPlanner"),
  fastHint: document.querySelector("#cpModelFastHint"),
}
const paletteFilter = document.querySelector("#paletteFilter")
const paletteList = document.querySelector("#paletteList")
const infoBackdrop = document.querySelector("#infoBackdrop")
const infoTitle = document.querySelector("#infoTitle")
const infoBody = document.querySelector("#infoBody")
const infoClose = document.querySelector("#infoClose")
const infoActions = document.querySelector("#infoActions")
const roleHint = document.querySelector("#roleHint")
const approvalHint = document.querySelector("#approvalHint")
const modelSuggestions = document.querySelector("#modelSuggestions")
const modelInputs = {
  fast: fastModelInput,
  smart: smartModelInput,
  planner: plannerModelInput,
}
const modelHints = {
  fast: document.querySelector("#fastHint"),
  smart: document.querySelector("#smartHint"),
  planner: document.querySelector("#plannerHint"),
}
const roleButton = document.querySelector("#roleButton")
const modelButton = document.querySelector("#modelButton")
const approvalButton = document.querySelector("#approvalButton")
const tokenSpeedLabel = document.querySelector("#tokenSpeed")
const metricsBar = document.querySelector("#metricsBar")
const metricTokens = document.querySelector("#metricTokens")
const metricContext = document.querySelector("#metricContext")
const metricSteps = document.querySelector("#metricSteps")
const metricThinking = document.querySelector("#metricThinking")
const metricElapsed = document.querySelector("#metricElapsed")

const CONFIG_STORAGE_KEY = "kvist.run-options.v1"
const MODEL_ROLES = ["fast", "smart", "planner"]

const BUILTIN_PROVIDER_LABELS = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  deepseek: "DeepSeek",
  openrouter: "OpenRouter",
  omniroute: "OmniRoute",
  freeflow: "FreeFlow LLM",
}

const ROLE_HINTS = {
  auto: "Kvist inspects each request and routes it to Coder, DevOps, or Chat.",
  coder: "Reads and edits code, then verifies. Best for implementing changes.",
  devops: "Shell-first: builds, tests, git, and environment or system tasks.",
  reader: "Read-only: inspects and reports, never writes files or runs commands.",
  chat: "Plain conversation — answers and explains, no file edits or shell.",
  universal: "Chat with the full tool set: answers, or reads/edits/runs as the request needs.",
}

const APPROVAL_HINTS = {
  ask: "Pause and ask for confirmation before each dangerous shell command.",
  deny: "Automatically refuse every dangerous command without asking.",
}

let workspace = ""
let running = false
let protocol = new ProtocolState()
let showDetails = false
let lastAssistantText = ""
let interruptDeadline = 0
let runtimeConfig = null
let configInfo = null
let configInfoStale = true
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
//
// Two quantities, deliberately kept apart: `tokensIn`/`tokensOut` are the run's
// cumulative spend — every step resends the whole conversation, so a 12k
// conversation over 25 steps legitimately spends 300k input tokens — while
// `contextTokens` is the size of the latest prompt, which is the number that
// matches what a user sees on screen.
let runStats = freshRunStats()

function freshRunStats() {
  return {
    tokensIn: 0,
    tokensOut: 0,
    contextTokens: 0,
    contextLimit: 0,
    steps: 0,
    reasoningSeconds: 0,
    startedAt: 0,
    frozenAt: 0,
  }
}

function normalizedRunOptions(value = {}) {
  const role = new Set(["auto", "coder", "devops", "chat", "universal"]).has(value.role)
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

// No real usage numbers survive a session switch — the core only reports
// them live, mid-run, via `usage` events. The best a reopened session can
// show is the size of what's actually stored for it, estimated with the same
// chars/4 fallback the live speed indicator uses. Good enough to replace a
// stale "0 in · 0 out" with a number that reflects the session just loaded.
function estimateSessionContextTokens(item) {
  let chars = 0
  for (const message of item?.chat_history || []) {
    if (typeof message?.content === "string") chars += message.content.length
  }
  for (const entry of item?.session_history || []) {
    if (!entry || typeof entry !== "object") continue
    if (typeof entry.goal === "string") chars += entry.goal.length
    if (typeof entry.result === "string") chars += entry.result.length
    for (const output of entry.outputs || []) {
      if (typeof output === "string") chars += output.length
    }
  }
  return chars / CHARS_PER_TOKEN
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

  const estimatedTokens = estimateSessionContextTokens(item)
  if (estimatedTokens > 0) {
    runStats.contextTokens = estimatedTokens
    renderRunStats()
  }

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
        const isActive = project.path === workspace && item.name === activeSessionName
        button.append(createElement("span", "navigation-label", item.title))
        if (isActive) button.classList.add("is-active")
        button.addEventListener("click", (event) => {
          event.stopPropagation()
          void selectSession(project.path, item.name)
        })
        // The core refuses to delete the active session, so only offer it on the
        // others; switch away first to remove the current one.
        if (!isActive) {
          const remove = createElement("span", "session-delete", "×")
          remove.title = "Delete session"
          remove.setAttribute("role", "button")
          remove.addEventListener("click", (event) => {
            event.stopPropagation()
            void deleteSession(project.path, item.name)
          })
          button.append(remove)
        }
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

const SPLASH_FADE_MS = 650
const SPLASH_DARK = { opacity: 0, filter: "brightness(0.15) blur(6px)" }
const SPLASH_BRIGHT = { opacity: 1, filter: "brightness(1.15) blur(0px)" }

function sessionSplashActive() {
  return !sessionSplash.classList.contains("is-hidden")
}

// The logo always finishes rising and fading even when the load itself is
// instant, so a fast switch never reads as a flicker.
async function withSessionSplash(load) {
  // The overlay starts visible so the launch never flashes the empty stage
  // before the first splash begins.
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    sessionSplash.classList.add("is-hidden")
    return load()
  }
  const timing = { duration: SPLASH_FADE_MS, easing: "ease-in-out", fill: "forwards" }
  sessionSplash.classList.remove("is-hidden")
  try {
    const appear = sessionSplashLogo.animate([SPLASH_DARK, SPLASH_BRIGHT], timing)
    const [result] = await Promise.all([load(), appear.finished])
    await sessionSplashLogo.animate([SPLASH_BRIGHT, SPLASH_DARK], timing).finished
    return result
  } finally {
    for (const animation of sessionSplashLogo.getAnimations()) animation.cancel()
    sessionSplash.classList.add("is-hidden")
  }
}

async function toggleProject(project) {
  if (running || sessionSplashActive() || !project.available) return
  if (project.path === workspace) {
    if (expandedProjects.has(project.path)) expandedProjects.delete(project.path)
    else expandedProjects.add(project.path)
    renderNavigation()
    return
  }
  try {
    expandedProjects.add(project.path)
    const state = await withSessionSplash(() => window.kvist.activateProject(project.path))
    updateNavigation(state)
    const selected = currentProject()
    displaySession(sessionItem(selected, selected?.status?.active_session))
    renderNavigation()
  } catch (error) {
    addError(error.message)
  }
}

async function selectSession(projectPath, name) {
  if (running || sessionSplashActive()) return
  try {
    expandedProjects.add(projectPath)
    const result = await withSessionSplash(() => window.kvist.switchSession(projectPath, name))
    updateNavigation(result.navigation)
    const project = currentProject()
    displaySession(sessionItem(project, name))
    renderNavigation()
  } catch (error) {
    addError(error.message)
  }
}

async function createNewSession() {
  if (running || sessionSplashActive() || !workspace) return
  try {
    const result = await withSessionSplash(() => window.kvist.newSession(workspace))
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

function renderContextMetric() {
  const used = runStats.contextTokens
  metricContext.classList.toggle("is-hidden", used <= 0)
  if (used <= 0) return
  const limit = runStats.contextLimit
  const share = limit > 0 ? Math.round((used / limit) * 100) : 0
  metricContext.textContent = limit > 0
    ? `ctx ${formatTokens(used)}/${formatTokens(limit)} · ${share}%`
    : `ctx ${formatTokens(used)}`
  metricContext.title =
    `Conversation currently sent to the model: ${Math.round(used)} tokens` +
    (limit > 0 ? ` of a ${Math.round(limit)} limit (the core trims above it)` : "")
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
  metricTokens.textContent = `Σ ${formatTokens(tokensIn)} in · ${formatTokens(tokensOut)} out`
  metricTokens.title =
    `Total spent this run across ${runStats.steps || 0} step(s) — the context is resent ` +
    `every step, so this is not the conversation size: ${Math.round(tokensIn)} in, ` +
    `${Math.round(runStats.tokensOut)} out${pending >= 1 ? ` (+~${Math.round(pending)} streaming)` : ""}`

  renderContextMetric()

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

function providerSummary(id) {
  if (!configInfo || !id) return null
  const all = [...(configInfo.providers || []), ...(configInfo.custom_providers || [])]
  return all.find((entry) => entry.id === id) || null
}

function providerLabel(id) {
  return providerSummary(id)?.label || BUILTIN_PROVIDER_LABELS[id] || id
}

function addingCustomProvider() {
  return providerSelect.value === "__new__"
}

function effectiveProviderId() {
  if (addingCustomProvider()) return customProviderInput.value.trim()
  if (providerSelect.value) return providerSelect.value
  return configInfo?.saved_provider || ""
}

function optionExists(value) {
  return Array.from(providerSelect.options).some((option) => option.value === value)
}

function providerOptionText(entry) {
  return `${entry.has_credentials ? "● " : "○ "}${entry.label}`
}

function renderProviderOptions() {
  const previous = providerSelect.value
  providerSelect.replaceChildren()
  const savedId = configInfo?.saved_provider || ""
  const savedSuffix = savedId ? ` · ${providerLabel(savedId)}` : ""
  providerSelect.append(new Option(`Saved default${savedSuffix}`, ""))

  const builtin = configInfo?.providers || []
  if (builtin.length) {
    const group = document.createElement("optgroup")
    group.label = "Providers"
    for (const entry of builtin) group.append(new Option(providerOptionText(entry), entry.id))
    providerSelect.append(group)
  } else {
    for (const [id, label] of Object.entries(BUILTIN_PROVIDER_LABELS)) {
      providerSelect.append(new Option(label, id))
    }
  }

  const custom = configInfo?.custom_providers || []
  if (custom.length) {
    const group = document.createElement("optgroup")
    group.label = "Custom providers"
    for (const entry of custom) group.append(new Option(providerOptionText(entry), entry.id))
    providerSelect.append(group)
  }

  providerSelect.append(new Option("＋ Add custom provider…", "__new__"))

  if (optionExists(previous)) providerSelect.value = previous
}

function setProviderStatus(text, tone) {
  providerStatus.textContent = text
  providerStatus.dataset.tone = tone
  providerStatus.classList.toggle("is-hidden", !text)
}

function detailLine(text, tone) {
  return createElement("div", tone ? `detail-line is-${tone}` : "detail-line", text)
}

function summariseModels(models) {
  const values = MODEL_ROLES.map((role) => models[role]).filter(Boolean)
  if (values.length === MODEL_ROLES.length && values.every((value) => value === values[0])) {
    return values[0]
  }
  return MODEL_ROLES.map((role) => models[role] || "—").join(" · ")
}

function renderProviderDetail() {
  const id = effectiveProviderId()
  const summary = providerSummary(id)
  providerDetail.replaceChildren()

  if (!configInfo) {
    setProviderStatus("Loading…", "neutral")
    return
  }
  if (!summary) {
    if (id.startsWith("custom:")) {
      setProviderStatus("New custom provider", "warn")
      providerDetail.append(detailLine(
        "Kvist will prompt for this provider's base URL and API key on the next run.",
      ))
    } else {
      setProviderStatus("", "neutral")
    }
    return
  }

  if (summary.has_credentials) setProviderStatus("API key ready", "ok")
  else setProviderStatus(`Missing ${summary.missing_keys.join(", ") || "credentials"}`, "warn")

  if (summary.base_url) providerDetail.append(detailLine(`Endpoint: ${summary.base_url}`))
  if (summary.requires_manual_models && MODEL_ROLES.every((role) => !summary.defaults[role])) {
    providerDetail.append(detailLine("No built-in defaults — set a model for each role below."))
  } else {
    providerDetail.append(detailLine(`Defaults: ${summariseModels(summary.defaults)}`))
  }
  if (!summary.has_credentials) {
    providerDetail.append(detailLine(
      "No key saved yet — add it in the terminal app or this provider fails closed.",
      "warn",
    ))
  }
}

function renderModelFields() {
  const summary = providerSummary(effectiveProviderId())
  modelSuggestions.replaceChildren()
  const seen = new Set()
  const suggestions = summary?.options?.length
    ? summary.options
    : MODEL_ROLES.map((role) => ({ value: summary?.saved_models?.[role] }))
  for (const option of suggestions) {
    if (!option.value || seen.has(option.value)) continue
    seen.add(option.value)
    modelSuggestions.append(new Option(option.label || "", option.value))
  }

  for (const role of MODEL_ROLES) {
    const fallback = summary?.saved_models?.[role] || summary?.defaults?.[role] || ""
    modelInputs[role].placeholder = fallback
      || (summary?.requires_manual_models ? "required" : "provider default")
    const hint = modelHints[role]
    if (!summary) {
      hint.textContent = ""
    } else if (summary.saved_models[role]) {
      hint.textContent = `Saved: ${summary.saved_models[role]}`
    } else if (summary.defaults[role]) {
      hint.textContent = `Default: ${summary.defaults[role]}`
    } else {
      hint.textContent = summary.requires_manual_models ? "Required for this provider" : ""
    }
  }
}

function refreshSettingsDetail() {
  customProviderField.hidden = !addingCustomProvider()
  roleHint.textContent = ROLE_HINTS[roleSelect.value] || ""
  approvalHint.textContent = APPROVAL_HINTS[approvalModeSelect.value] || ""
  renderProviderDetail()
  renderModelFields()
}

function populateSettings() {
  renderProviderOptions()
  const provider = runOptions.provider
  if (provider && optionExists(provider)) {
    providerSelect.value = provider
    customProviderInput.value = ""
  } else if (provider) {
    providerSelect.value = "__new__"
    customProviderInput.value = provider
  } else {
    providerSelect.value = ""
    customProviderInput.value = ""
  }
  roleSelect.value = runOptions.role
  approvalModeSelect.value = runOptions.approvalMode
  for (const role of MODEL_ROLES) modelInputs[role].value = runOptions.models[role]
  settingsError.textContent = ""
  refreshSettingsDetail()
}

async function showSettings() {
  if (running) return
  hidePalette()
  await ensureFreshConfigInfo()
  settingsBackdrop.classList.remove("is-hidden")
  populateSettings()
  providerSelect.focus()
}

async function ensureFreshConfigInfo() {
  if (!configInfoStale && configInfo) return
  setProviderStatus("Loading…", "neutral")
  try {
    configInfo = await window.kvist.configInfo()
    configInfoStale = false
  } catch (error) {
    settingsError.textContent = `Could not read saved configuration: ${error.message}`
  }
}

function hideSettings() {
  settingsBackdrop.classList.add("is-hidden")
  settingsError.textContent = ""
  if (!promptInput.disabled) promptInput.focus()
}

// ── Custom provider creation ────────────────────────────────────────────────
// The terminal walks the user through a wizard; the GUI collects the same
// fields in one form and hands them to `--config-op add-custom-provider`, which
// runs `custom_provider.normalize_custom_provider_payload` — the single source
// of truth for what a saved provider looks like.

function syncCustomProviderForm() {
  const openai = cpFields.protocol.value === "openai"
  cpFields.addV1Field.hidden = !openai
  const one = cpFields.oneModel.checked
  for (const role of ["smart", "planner"]) {
    cpFields[role].disabled = one
    cpFields[role].closest(".field").hidden = one
  }
  cpFields.fastHint.textContent = one ? "Used for every role" : ""
}

function openCustomProviderForm() {
  customProviderError.textContent = ""
  customProviderForm.reset()
  cpFields.addV1.checked = true
  cpFields.oneModel.checked = true
  syncCustomProviderForm()
  cpConfirmView.hidden = false
  cpFormView.hidden = true
  hidePalette()
  customProviderBackdrop.classList.remove("is-hidden")
  cpConfirmContinue.focus()
}

function revealCustomProviderFields() {
  cpConfirmView.hidden = true
  cpFormView.hidden = false
  cpFields.name.focus()
}

function hideCustomProviderForm() {
  customProviderBackdrop.classList.add("is-hidden")
  customProviderError.textContent = ""
}

function customProviderPayload() {
  let baseUrl = cpFields.baseUrl.value.trim()
  const openai = cpFields.protocol.value === "openai"
  if (openai && cpFields.addV1.checked && baseUrl) {
    const withScheme = /^https?:\/\//i.test(baseUrl) ? baseUrl : `https://${baseUrl}`
    try {
      if (!new URL(withScheme).pathname.replace(/\/+$/, "").replace(/^\/+/, "")) {
        baseUrl = `${baseUrl.replace(/\/+$/, "")}/v1`
      }
    } catch {
      // Let the backend reject a malformed URL with its own message.
    }
  }
  const models = {}
  for (const role of MODEL_ROLES) {
    models[role] = cpFields.oneModel.checked
      ? cpFields.fast.value.trim()
      : cpFields[role].value.trim()
  }
  return {
    name: cpFields.name.value.trim(),
    label: cpFields.label.value.trim(),
    protocol: cpFields.protocol.value,
    base_url: baseUrl,
    api_key: cpFields.apiKey.value.trim(),
    streaming: cpFields.streaming.checked,
    models,
  }
}

async function submitCustomProvider() {
  customProviderError.textContent = "Creating…"
  let result
  try {
    result = await window.kvist.addCustomProvider(customProviderPayload())
  } catch (error) {
    customProviderError.textContent = error.message
    return
  }
  configInfo = result.info
  configInfoStale = false
  renderProviderOptions()
  if (optionExists(result.provider_id)) providerSelect.value = result.provider_id
  customProviderInput.value = ""
  hideCustomProviderForm()
  refreshSettingsDetail()
}

let providerChoiceBeforeNew = ""

function handleProviderSelectChange() {
  if (providerSelect.value !== "__new__") {
    providerChoiceBeforeNew = providerSelect.value
    refreshSettingsDetail()
    return
  }
  providerSelect.value = providerChoiceBeforeNew
  refreshSettingsDetail()
  openCustomProviderForm()
}

// Pin the models the dialog is actually showing for the chosen provider, so a
// launch is fully determined by these settings. A blank field means "whatever
// this provider shows" (its saved override, else its built-in default) — not
// "whatever the terminal happened to use last", which is a different provider's
// leftover and makes the run lie about which model is serving it.
function resolvedModels() {
  const summary = providerSummary(effectiveProviderId())
  const pick = (role, typed) =>
    typed
    || summary?.saved_models?.[role]
    || summary?.defaults?.[role]
    || ""
  return {
    fast: pick("fast", fastModelInput.value.trim()),
    smart: pick("smart", smartModelInput.value.trim()),
    planner: pick("planner", plannerModelInput.value.trim()),
  }
}

function saveSettings() {
  const models = resolvedModels()
  const modelValues = Object.values(models)
  if (modelValues.some(Boolean) && !modelValues.every(Boolean)) {
    settingsError.textContent = "Fill all three model fields, or clear all three."
    return false
  }
  const customProvider = customProviderInput.value.trim()
  if (addingCustomProvider() && !/^custom:[a-z0-9][a-z0-9_-]{0,63}$/i.test(customProvider)) {
    settingsError.textContent = "Custom provider ID must look like custom:provider-name."
    return false
  }
  runOptions = normalizedRunOptions({
    provider: addingCustomProvider() ? customProvider : providerSelect.value,
    role: roleSelect.value,
    approvalMode: approvalModeSelect.value,
    models,
  })
  localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(runOptions))
  runtimeConfig = null
  configInfoStale = true
  updateConfigChips()
  return true
}

// A status message stays up at least this long before a newer one replaces
// it, so a batch of fast actions does not flash its message past unread.
const STATUS_HOLD_MS = 2500
let statusShownAt = 0
let statusHoldTimer = null

function showStatus(event) {
  clearTimeout(statusHoldTimer)
  const text = event.user_message || event.status || "Working…"
  const show = () => {
    if (activityText.textContent !== text) statusShownAt = Date.now()
    setActivity(text, { busy: !event.is_error, progress: event.progress, error: event.is_error })
  }
  const wait = activityText.textContent === text ? 0 : statusShownAt + STATUS_HOLD_MS - Date.now()
  if (wait > 0) statusHoldTimer = setTimeout(show, wait)
  else show()
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

// The calls of one model response (one batch_id) share a group; its first step
// carries the response's words for the person, shown once above the calls.
let stepBatch = { id: "", container: null }

function stepContainer(event) {
  const id = event.batch_id || ""
  if (id && id === stepBatch.id && stepBatch.container?.isConnected) return stepBatch.container
  const container = createElement("div", "step-batch")
  const say = String(event.user_message || "").trim()
  if (say && id) container.append(createElement("div", "step-say", say))
  messages.append(container)
  stepBatch = { id, container }
  return container
}

function addStep(event) {
  enterConversation()
  const details = createElement("details", "message step-card")
  details.open = showDetails
  const summary = createElement("summary")
  summary.append(
    createElement("span", "step-action", event.action_type || event.plan_step || "step"),
    createElement("span", "", event.action_description || event.user_message || `Step ${event.step}`),
  )
  if (event.reasoning_seconds > 0) {
    summary.append(
      createElement("span", "step-thought", `🧠 ${Math.max(1, Math.round(event.reasoning_seconds))}s`),
    )
  }
  if (event.plan_step) summary.append(createElement("span", "step-phase", event.plan_step))
  const content = createElement("div", "step-content")
  if (event.thought) content.append(createElement("div", "", event.thought))
  if (event.action_args && Object.keys(event.action_args).length) {
    content.append(createElement("pre", "", JSON.stringify(event.action_args, null, 2)))
  }
  details.append(summary, content)
  stepContainer(event).append(details)
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
      clearTimeout(statusHoldTimer)
      setActivity("Kvist is working…", { busy: true })
      break
    case "live_stop":
      clearTimeout(statusHoldTimer)
      setActivity("Finishing…", { busy: true })
      break
    case "status":
      showStatus(event)
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
    case "usage": {
      runStats.tokensIn += Number(event.input_tokens) || 0
      runStats.tokensOut += Number(event.output_tokens) || 0
      // Context replaces rather than accumulates, and only calls carrying the
      // conversation report it (0 = keep the last known size).
      const context = Number(event.context_tokens) || 0
      if (context > 0) runStats.contextTokens = context
      runStats.contextLimit = Math.max(runStats.contextLimit, Number(event.context_limit) || 0)
      renderRunStats()
      break
    }
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
      // An older core sends no outcome; only an explicit "incomplete" is not done.
      if (event.outcome === "incomplete") setActivity("Finished, not done", { error: true })
      else setActivity("Done", { progress: 100 })
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

function activeCheckpointAvailable() {
  const project = currentProject()
  if (sessionItem(project, activeSessionName)?.has_checkpoint) return true
  // A launch opens its own session, so the interrupted run usually sits in
  // another one; the core walks there on resume.
  return Boolean(project?.status?.resumable_session)
}

async function resumeCheckpoint() {
  if (running || !workspace) return
  if (!activeCheckpointAvailable()) {
    setActivity("No saved checkpoint to resume", { error: true })
    return
  }
  protocol = new ProtocolState()
  lastAssistantText = ""
  resetTokenSpeed()
  resetRunStats()
  runStats.startedAt = Date.now()
  requestHost.replaceChildren()
  enterConversation()
  setRunning(true)
  setActivity("Resuming checkpoint…", { busy: true, progress: 0 })
  try {
    await window.kvist.resume(workspace, runOptions)
  } catch (error) {
    setRunning(false)
    addError(error.message)
    setActivity("Could not resume", { error: true })
  }
}

async function deleteSession(projectPath, name) {
  if (running || !name) return
  showConfirm(
    "Delete session",
    `Delete “${name}”? Its chat history and checkpoint are removed permanently.`,
    "Delete",
    async () => {
      try {
        const result = await window.kvist.deleteSession(projectPath, name)
        updateNavigation(result.navigation)
        const project = currentProject()
        if (name === activeSessionName) {
          displaySession(sessionItem(project, project?.status?.active_session))
        }
        renderNavigation()
      } catch (error) {
        addError(error.message)
      }
    },
  )
}

async function showChanges() {
  if (!workspace) return
  setActivity("Reading changes…", { busy: true })
  try {
    const result = await window.kvist.changes()
    showInfo("Agent file changes", result.report || "No tracked agent changes.", { mono: true })
    setActivity("Ready")
  } catch (error) {
    addError(error.message)
    setActivity("Could not read changes", { error: true })
  }
}

function confirmRevert() {
  if (running || !workspace) return
  showConfirm(
    "Revert agent changes",
    "Restore every file the agent changed (across all sessions) to its recorded baseline. "
      + "Your own later edits are kept. Continue?",
    "Revert",
    async () => {
      setActivity("Reverting…", { busy: true })
      try {
        const result = await window.kvist.revert()
        const changed = (result.reverted || []).filter((item) => item.action !== "skipped")
        const summary = changed.length
          ? changed.map((item) => `${item.action}: ${item.path}`).join("\n")
          : "No files needed reverting."
        showInfo("Revert complete", summary, { mono: true })
        setActivity("Reverted")
        void refreshNavigation()
      } catch (error) {
        addError(error.message)
        setActivity("Could not revert", { error: true })
      }
    },
  )
}

function showAbout() {
  const lines = [
    "Kvist — local, terminal-native coding agent.",
    "",
    `Protocol ${protocolLabel.textContent}`,
    `Workspace: ${workspace || "none"}`,
    "",
    "Ctrl+P — command palette",
    "Ctrl+N — new chat",
    "Ctrl+M — run configuration",
    "Esc — interrupt run or close a dialog",
  ]
  showInfo("About Kvist", lines.join("\n"))
}

function showInfo(title, body, { mono = false } = {}) {
  infoTitle.textContent = title
  infoBody.textContent = body
  infoBody.classList.toggle("is-mono", mono)
  infoActions.replaceChildren(actionButton("Close", "", hideInfo))
  infoBackdrop.classList.remove("is-hidden")
  infoActions.querySelector("button")?.focus()
}

function showConfirm(title, body, confirmLabel, onConfirm) {
  infoTitle.textContent = title
  infoBody.textContent = body
  infoBody.classList.remove("is-mono")
  const cancel = actionButton("Cancel", "", hideInfo)
  const confirm = actionButton(confirmLabel, "danger-button", () => {
    hideInfo()
    void onConfirm()
  })
  infoActions.replaceChildren(cancel, confirm)
  infoBackdrop.classList.remove("is-hidden")
  cancel.focus()
}

function hideInfo() {
  infoBackdrop.classList.add("is-hidden")
  infoBody.classList.remove("is-mono")
  if (!promptInput.disabled) promptInput.focus()
}

const PALETTE_COMMANDS = [
  { id: "new", label: "New chat", hint: "Reset the session and start fresh", keys: "ctrl+n" },
  {
    id: "resume",
    label: "Resume checkpoint",
    hint: "Continue the latest unfinished task",
    enabled: () => activeCheckpointAvailable(),
  },
  {
    id: "settings",
    label: "Provider, models & role",
    hint: "Pick provider, models, role and approvals",
    keys: "ctrl+m",
  },
  { id: "workspace", label: "Open project…", hint: "Switch to another workspace folder" },
  { id: "changes", label: "Show agent changes", hint: "Diff of every file the agent changed" },
  {
    id: "revert",
    label: "Revert agent changes",
    hint: "Restore changed files to their baselines",
    enabled: () => !running,
  },
  { id: "details", label: "Toggle tool details", hint: "Expand or collapse step cards" },
  {
    id: "interrupt",
    label: "Interrupt Kvist",
    hint: "Stop the current run",
    keys: "esc",
    enabled: () => running,
  },
  { id: "about", label: "About Kvist", hint: "Shortcuts and build info" },
]

async function runCommand(id) {
  hidePalette()
  if (id === "new") await createNewSession()
  else if (id === "resume") await resumeCheckpoint()
  else if (id === "settings") await showSettings()
  else if (id === "workspace") { if (!running) await chooseProject() }
  else if (id === "changes") await showChanges()
  else if (id === "revert") confirmRevert()
  else if (id === "details") toggleDetails()
  else if (id === "interrupt") await interrupt()
  else if (id === "about") showAbout()
}

function renderPalette() {
  const query = paletteFilter.value.trim().toLowerCase()
  paletteList.replaceChildren()
  const items = PALETTE_COMMANDS.filter((command) => {
    if (command.enabled && !command.enabled()) return false
    if (!query) return true
    return `${command.label} ${command.hint}`.toLowerCase().includes(query)
  })
  if (!items.length) {
    paletteList.append(createElement("div", "empty-navigation", "No matching commands"))
    return
  }
  for (const command of items) {
    const button = createElement("button", "palette-item")
    button.type = "button"
    const copy = createElement("span", "palette-copy")
    copy.append(
      createElement("span", "palette-label", command.label),
      createElement("small", "", command.hint),
    )
    button.append(copy)
    if (command.keys) button.append(createElement("kbd", "", command.keys))
    button.addEventListener("click", () => void runCommand(command.id))
    paletteList.append(button)
  }
}

function showPalette() {
  paletteFilter.value = ""
  renderPalette()
  paletteBackdrop.classList.remove("is-hidden")
  paletteFilter.focus()
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

for (const control of [roleSelect, approvalModeSelect]) {
  control.addEventListener("change", refreshSettingsDetail)
}
providerSelect.addEventListener("change", handleProviderSelectChange)
customProviderInput.addEventListener("input", refreshSettingsDetail)

addCustomProviderButton.addEventListener("click", openCustomProviderForm)
customProviderClose.addEventListener("click", hideCustomProviderForm)
customProviderCancel.addEventListener("click", hideCustomProviderForm)
cpConfirmCancel.addEventListener("click", hideCustomProviderForm)
cpConfirmContinue.addEventListener("click", revealCustomProviderFields)
customProviderBackdrop.addEventListener("click", (event) => {
  if (event.target === customProviderBackdrop) hideCustomProviderForm()
})
customProviderBackdrop.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return
  event.preventDefault()
  event.stopPropagation()
  hideCustomProviderForm()
})
cpFields.protocol.addEventListener("change", syncCustomProviderForm)
cpFields.oneModel.addEventListener("change", syncCustomProviderForm)
customProviderForm.addEventListener("submit", (event) => {
  event.preventDefault()
  void submitCustomProvider()
})

settingsClose.addEventListener("click", hideSettings)
settingsCancel.addEventListener("click", hideSettings)
settingsReset.addEventListener("click", () => {
  runOptions = normalizedRunOptions()
  runtimeConfig = null
  configInfoStale = true
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

paletteFilter.addEventListener("input", renderPalette)
paletteFilter.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return
  event.preventDefault()
  paletteList.querySelector("button")?.click()
})

infoClose.addEventListener("click", hideInfo)
infoBackdrop.addEventListener("click", (event) => {
  if (event.target === infoBackdrop) hideInfo()
})
infoBackdrop.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return
  event.preventDefault()
  event.stopPropagation()
  hideInfo()
})

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
    && infoBackdrop.classList.contains("is-hidden")
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
installCursorGlow()

function installCursorGlow() {
  const glow = document.querySelector("#cursorGlow")
  if (!glow || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return
  const pos = { x: window.innerWidth / 2, y: window.innerHeight / 2 }
  const target = { x: pos.x, y: pos.y }
  let frame = 0

  const paint = () => {
    glow.style.transform = `translate3d(${pos.x}px, ${pos.y}px, 0)`
  }
  const step = () => {
    pos.x += (target.x - pos.x) * 0.16
    pos.y += (target.y - pos.y) * 0.16
    paint()
    frame = (Math.abs(target.x - pos.x) > 0.4 || Math.abs(target.y - pos.y) > 0.4)
      ? requestAnimationFrame(step)
      : 0
  }

  paint()
  glow.classList.add("is-active")
  window.addEventListener("pointermove", (event) => {
    target.x = event.clientX
    target.y = event.clientY
    glow.classList.add("is-active")
    if (!frame) frame = requestAnimationFrame(step)
  })
  document.addEventListener("mouseleave", () => glow.classList.remove("is-active"))
  window.addEventListener("blur", () => glow.classList.remove("is-active"))
}

// ── API settings dialog ──────────────────────────────────────────────────

async function openApiSettingsDialog() {
  hideGlobalSettings()
  setActivity("Loading API settings…", { busy: true })
  let settings
  try {
    const result = await window.kvist.getApiSettings()
    settings = result.settings
  } catch (error) {
    addError(error.message)
    setActivity("Could not load API settings", { error: true })
    return
  }
  setActivity("Ready")

  const backdrop = document.getElementById("apiSettingsBackdrop")
  const body = document.getElementById("apiSettingsBody")
  const errorEl = document.getElementById("apiSettingsError")
  body.replaceChildren()
  errorEl.textContent = ""

  const inputs = {}

  for (const setting of settings) {
    const row = createElement("div", "api-setting-row")
    const header = createElement("div", "api-setting-header")
    const label = createElement("div", "api-setting-label", setting.label)
    const desc = createElement("div", "api-setting-desc", setting.description)
    header.append(label, desc)
    row.append(header)

    if (setting.kind === "bool") {
      const toggle = createElement("button", "api-toggle")
      toggle.type = "button"
      toggle.role = "switch"
      toggle.ariaChecked = String(setting.value === true)
      toggle.classList.toggle("is-on", setting.value === true)
      const knob = createElement("span", "api-toggle-knob")
      toggle.append(knob)
      toggle.addEventListener("click", () => {
        const on = toggle.ariaChecked !== "true"
        toggle.ariaChecked = String(on)
        toggle.classList.toggle("is-on", on)
        inputs[setting.key] = on
      })
      inputs[setting.key] = setting.value
      row.append(toggle)
    } else {
      const input = createElement("input", "api-setting-input")
      input.type = "number"
      if (setting.kind === "float") input.step = "0.1"
      else input.step = "1"
      if (setting.minimum) input.min = String(setting.minimum)
      if (setting.maximum) input.max = String(setting.maximum)
      // Show the current value, or empty for the zero-as-mode case
      if (setting.zero_label && setting.value === setting.default && setting.default === 0) {
        input.placeholder = setting.zero_label
      } else if (setting.zero_label) {
        input.placeholder = setting.zero_label
        input.value = setting.value === 0 ? "" : String(setting.value)
      } else {
        input.value = String(setting.value)
      }
      const suffix = createElement("span", "api-setting-unit", setting.unit || "")
      const inputRow = createElement("div", "api-setting-input-row")
      inputRow.append(input, suffix)
      row.append(inputRow)

      inputs[setting.key] = setting.value
      input.addEventListener("input", () => {
        const raw = input.value.trim()
        if (raw === "" && setting.zero_label) inputs[setting.key] = 0
        else if (raw === "") inputs[setting.key] = setting.default
        else if (setting.kind === "float") inputs[setting.key] = parseFloat(raw)
        else inputs[setting.key] = parseInt(raw, 10)
      })

      if (setting.env_override !== undefined) {
        const envNote = createElement("div", "api-setting-env-note",
          `Overridden by ${setting.env_var || "env"} = ${setting.env_override}`)
        row.append(envNote)
      }
    }

    body.append(row)
  }

  backdrop.classList.remove("is-hidden")
  body.querySelector("input")?.focus()

  const saveBtn = document.getElementById("apiSettingsSave")
  const cancelBtn = document.getElementById("apiSettingsCancel")
  const closeBtn = document.getElementById("apiSettingsClose")

  const cleanup = () => {
    backdrop.classList.add("is-hidden")
    if (!promptInput.disabled) promptInput.focus()
    saveBtn.replaceWith(saveBtn.cloneNode(true))
    cancelBtn.replaceWith(cancelBtn.cloneNode(true))
    closeBtn.replaceWith(closeBtn.cloneNode(true))
  }

  saveBtn.addEventListener("click", async () => {
    errorEl.textContent = "Saving…"
    try {
      await window.kvist.updateApiSettings(inputs)
      errorEl.textContent = ""
      cleanup()
      setActivity("API settings saved")
    } catch (error) {
      errorEl.textContent = error.message
    }
  }, { once: true })
  cancelBtn.addEventListener("click", cleanup, { once: true })
  closeBtn.addEventListener("click", cleanup, { once: true })
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) cleanup()
  })
  backdrop.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return
    event.preventDefault()
    event.stopPropagation()
    cleanup()
  })
}

function confirmFullReset() {
  if (running) return
  hideGlobalSettings()
  showConfirm(
    "Reset everything",
    "This wipes ~/.kvist/config.json entirely: every saved API key, custom "
      + "provider, model selection and preference. It cannot be undone.",
    "Continue",
    () => {
      // Step 2: Final confirmation modal with checkbox and 'Reset' input verification
      infoTitle.textContent = "Final confirmation"
      infoBody.textContent = "Are you absolutely sure? All provider settings, API keys and custom providers will be erased permanently."
      infoBody.classList.remove("is-mono")

      const box = createElement("div", "reset-confirm-box")

      const checkLabel = createElement("label", "reset-confirm-checkbox-label")
      const checkbox = document.createElement("input")
      checkbox.type = "checkbox"
      checkLabel.append(checkbox, document.createTextNode("I understand the consequences"))

      const inputWrap = createElement("div", "reset-confirm-input-wrapper")
      const inputLabel = createElement("div", "reset-confirm-input-label", 'Type "Reset" to confirm:')
      const textInput = document.createElement("input")
      textInput.type = "text"
      textInput.className = "reset-confirm-input"
      textInput.placeholder = "Reset"
      inputWrap.append(inputLabel, textInput)

      box.append(checkLabel, inputWrap)
      infoBody.append(box)

      const cancelBtn = actionButton("Cancel", "", hideInfo)
      const wipeBtn = actionButton("Wipe everything", "danger-button", async () => {
        hideInfo()
        setActivity("Resetting…", { busy: true })
        try {
          await window.kvist.fullReset()
          configInfoStale = true
          runOptions = normalizedRunOptions()
          localStorage.removeItem(CONFIG_STORAGE_KEY)
          updateConfigChips()
          setActivity("Full reset complete")
        } catch (error) {
          addError(error.message)
          setActivity("Could not reset", { error: true })
        }
      })

      wipeBtn.disabled = true

      function updateWipeState() {
        const checked = checkbox.checked
        const textValue = textInput.value.trim()
        wipeBtn.disabled = !(checked && textValue.toLowerCase() === "reset")
      }

      checkbox.addEventListener("change", updateWipeState)
      textInput.addEventListener("input", updateWipeState)

      infoActions.replaceChildren(cancelBtn, wipeBtn)
      infoBackdrop.classList.remove("is-hidden")
      textInput.focus()
    },
  )
}

// ── Global settings panel ────────────────────────────────────────────────

function showGlobalSettings() {
  if (running) return
  hidePalette()
  void ensureFreshConfigInfo().then(() => {
    globalSettingsBackdrop.classList.remove("is-hidden")
    populateGlobalSettings()
  })
}

function hideGlobalSettings() {
  globalSettingsBackdrop.classList.add("is-hidden")
  if (!promptInput.disabled) promptInput.focus()
}

function populateGlobalSettings() {
  if (!configInfo) return

  // Provider panel
  const providerPanel = document.querySelector("#settingsPanelProvider .settings-panel-body")
  providerPanel.innerHTML = ""

  const providerSection = createElement("div", "settings-block")
  const providerLabelElem = createElement("label", "field")
  providerLabelElem.innerHTML = `<span>Active Provider</span>`

  const providerSelectGlobal = createElement("select", "")
  providerSelectGlobal.id = "globalProviderSelect"
  const savedId = configInfo?.saved_provider || ""
  const savedLabel = providerLabel(savedId)
  const savedSuffix = savedId ? ` · ${savedLabel}` : ""
  providerSelectGlobal.append(new Option(`Saved default${savedSuffix}`, ""))

  const builtin = configInfo?.providers || []
  if (builtin.length) {
    const group = document.createElement("optgroup")
    group.label = "Providers"
    for (const entry of builtin) group.append(new Option(providerOptionText(entry), entry.id))
    providerSelectGlobal.append(group)
  }

  const custom = configInfo?.custom_providers || []
  if (custom.length) {
    const group = document.createElement("optgroup")
    group.label = "Custom providers"
    for (const entry of custom) group.append(new Option(providerOptionText(entry), entry.id))
    providerSelectGlobal.append(group)
  }

  providerLabelElem.append(providerSelectGlobal)
  providerSection.append(providerLabelElem)

  const providerInfo = createElement("div", "provider-detail")
  providerInfo.id = "globalProviderDetail"
  providerSection.append(providerInfo)

  providerPanel.append(providerSection)

  // Models panel
  const modelsPanel = document.querySelector("#settingsPanelModels .settings-panel-body")
  modelsPanel.innerHTML = ""

  const currentProviderId = providerSelectGlobal ? providerSelectGlobal.value : runOptions.provider
  const currentSummary = providerSummary(currentProviderId || configInfo?.saved_provider)

  const modelsSection = createElement("div", "settings-block")

  // Show model options if provider has them, otherwise text inputs
  const hasOptions = currentSummary?.options?.length > 0
  const requiresManual = currentSummary?.requires_manual_models

  if (hasOptions) {
    // Use dropdowns for providers with model catalogs
    const modelsGrid = createElement("div", "field-grid three")

    for (const role of MODEL_ROLES) {
      const field = createElement("label", "field")
      field.innerHTML = `<span>${role.charAt(0).toUpperCase() + role.slice(1)}</span>`

      const select = createElement("select")
      select.id = `globalModel${role.charAt(0).toUpperCase() + role.slice(1)}`

      // Add empty option for "use provider default"
      select.append(new Option("Provider default", ""))

      // Add all available models
      for (const option of currentSummary.options) {
        select.append(new Option(option.label || option.value, option.value))
      }

      // Set current value
      const currentValue = runOptions.models[role] || currentSummary?.saved_models?.[role] || ""
      if (currentValue) select.value = currentValue

      field.append(select)

      const hint = createElement("small", "field-hint")
      const defaultModel = currentSummary?.defaults?.[role]
      if (defaultModel) {
        hint.textContent = `Default: ${defaultModel}`
      }
      field.append(hint)

      modelsGrid.append(field)
    }

    modelsSection.append(modelsGrid)
  } else {
    // Text inputs for custom/OmniRoute/OpenRouter
    const modelsGrid = createElement("div", "field-grid three")

    for (const role of MODEL_ROLES) {
      const field = createElement("label", "field")
      field.innerHTML = `<span>${role.charAt(0).toUpperCase() + role.slice(1)}</span>`
      const input = createElement("input")
      input.type = "text"
      input.id = `globalModel${role.charAt(0).toUpperCase() + role.slice(1)}`
      input.autocomplete = "off"
      input.value = runOptions.models[role] || ""
      input.placeholder = requiresManual ? "Required" : "Optional"
      field.append(input)
      const hint = createElement("small", "field-hint")
      if (requiresManual) {
        hint.textContent = "This provider requires manual model entry"
      }
      field.append(hint)
      modelsGrid.append(field)
    }

    modelsSection.append(modelsGrid)
  }

  modelsPanel.append(modelsSection)

  // Role panel
  const rolePanel = document.querySelector("#settingsPanelRole .settings-panel-body")
  rolePanel.innerHTML = ""

  const roleSection = createElement("div", "settings-block")
  const roleGrid = createElement("div", "field-grid two")

  const roleField = createElement("label", "field")
  roleField.innerHTML = `<span>Role</span>`
  const roleSelectGlobal = createElement("select")
  roleSelectGlobal.id = "globalRoleSelect"
  roleSelectGlobal.innerHTML = `
    <option value="auto">Auto (route each request)</option>
    <option value="coder">Coder</option>
    <option value="devops">DevOps</option>
    <option value="reader">Reader</option>
    <option value="chat">Chat</option>
    <option value="universal">Universal</option>
  `
  roleField.append(roleSelectGlobal)
  const roleHintGlobal = createElement("small", "field-hint")
  roleHintGlobal.id = "globalRoleHint"
  roleField.append(roleHintGlobal)
  roleGrid.append(roleField)

  const approvalField = createElement("label", "field")
  approvalField.innerHTML = `<span>Dangerous commands</span>`
  const approvalSelectGlobal = createElement("select")
  approvalSelectGlobal.id = "globalApprovalSelect"
  approvalSelectGlobal.innerHTML = `
    <option value="ask">Ask every time</option>
    <option value="deny">Always deny</option>
  `
  approvalField.append(approvalSelectGlobal)
  const approvalHintGlobal = createElement("small", "field-hint")
  approvalHintGlobal.id = "globalApprovalHint"
  approvalField.append(approvalHintGlobal)
  roleGrid.append(approvalField)

  roleSection.append(roleGrid)
  rolePanel.append(roleSection)

  // Advanced panel
  const advancedPanel = document.querySelector("#settingsPanelAdvanced .settings-panel-body")
  advancedPanel.innerHTML = ""

  // ── Requests section ──
  const requestsSection = createElement("div", "settings-block")
  const requestsRow = createElement("div", "advanced-row")
  const requestsText = createElement("div", "advanced-row-text")
  requestsText.append(
    createElement("div", "advanced-row-title", "Requests"),
    createElement("div", "advanced-row-hint", "Pacing, timeouts, context window"),
  )
  const requestsBtn = createElement("button", "advanced-btn")
  requestsBtn.textContent = "Open"
  requestsBtn.type = "button"
  requestsBtn.addEventListener("click", () => openApiSettingsDialog())
  requestsRow.append(requestsText, requestsBtn)
  requestsSection.append(requestsRow)

  // ── Changes section ──
  const changesRow = createElement("div", "advanced-row")
  const changesText = createElement("div", "advanced-row-text")
  changesText.append(
    createElement("div", "advanced-row-title", "Changes"),
    createElement("div", "advanced-row-hint", "Files the agent changed"),
  )
  const changesActions = createElement("div", "advanced-row-actions")
  const showChangesBtn = createElement("button", "advanced-btn")
  showChangesBtn.textContent = "Show"
  showChangesBtn.type = "button"
  showChangesBtn.addEventListener("click", () => {
    hideGlobalSettings()
    void showChanges()
  })
  const revertBtn = createElement("button", "advanced-btn")
  revertBtn.textContent = "Revert"
  revertBtn.type = "button"
  revertBtn.addEventListener("click", () => {
    hideGlobalSettings()
    confirmRevert()
  })
  changesActions.append(showChangesBtn, revertBtn)
  changesRow.append(changesText, changesActions)
  requestsSection.append(changesRow)

  // ── Reset section ──
  const resetRow = createElement("div", "advanced-row")
  const resetText = createElement("div", "advanced-row-text")
  resetText.append(
    createElement("div", "advanced-row-title", "Reset everything"),
    createElement("div", "advanced-row-hint", "Erases every saved provider and API key"),
  )
  const resetBtn = createElement("button", "advanced-btn is-danger")
  resetBtn.textContent = "Reset"
  resetBtn.type = "button"
  resetBtn.addEventListener("click", () => confirmFullReset())
  resetRow.append(resetText, resetBtn)
  requestsSection.append(resetRow)

  advancedPanel.append(requestsSection)

  // Populate with current values
  if (runOptions.provider && optionExists(runOptions.provider)) {
    providerSelectGlobal.value = runOptions.provider
  }

  const globalRoleSelect = document.getElementById("globalRoleSelect")
  const globalApprovalSelect = document.getElementById("globalApprovalSelect")
  if (globalRoleSelect) globalRoleSelect.value = runOptions.role
  if (globalApprovalSelect) globalApprovalSelect.value = runOptions.approvalMode

  // Update hints
  const globalRoleHint = document.getElementById("globalRoleHint")
  const globalApprovalHint = document.getElementById("globalApprovalHint")
  if (globalRoleHint) globalRoleHint.textContent = ROLE_HINTS[runOptions.role] || ""
  if (globalApprovalHint) globalApprovalHint.textContent = APPROVAL_HINTS[runOptions.approvalMode] || ""

  // Set up event listeners
  providerSelectGlobal.addEventListener("change", () => {
    updateGlobalProviderDetail()
    // Re-render Models panel with new provider's options
    const modelsNavItem = document.querySelector(".settings-nav-item[data-panel='models']")
    if (modelsNavItem) {
      const currentPanel = document.querySelector(".settings-content-panel.is-active")
      const isModelsActive = currentPanel && currentPanel.id === "settingsPanelModels"
      populateGlobalSettings()
      if (isModelsActive) {
        switchSettingsPanel("models")
      }
    }
  })

  if (globalRoleSelect) {
    globalRoleSelect.addEventListener("change", () => {
      const hint = document.getElementById("globalRoleHint")
      if (hint) hint.textContent = ROLE_HINTS[globalRoleSelect.value] || ""
    })
  }

  if (globalApprovalSelect) {
    globalApprovalSelect.addEventListener("change", () => {
      const hint = document.getElementById("globalApprovalHint")
      if (hint) hint.textContent = APPROVAL_HINTS[globalApprovalSelect.value] || ""
    })
  }

  updateGlobalProviderDetail()
}

function updateGlobalProviderDetail() {
  const providerSelectElem = document.getElementById("globalProviderSelect")
  const detailContainer = document.getElementById("globalProviderDetail")
  if (!providerSelectElem || !detailContainer) return

  const id = providerSelectElem.value
  const summary = providerSummary(id)
  detailContainer.replaceChildren()

  if (!summary) return

  if (summary.has_credentials) {
    detailContainer.append(detailLine("✓ API key ready", "ok"))
  } else {
    detailContainer.append(detailLine(`Missing ${summary.missing_keys.join(", ") || "credentials"}`, "warn"))
  }

  if (summary.base_url) {
    detailContainer.append(detailLine(`Endpoint: ${summary.base_url}`))
  }

  detailContainer.append(detailLine(`Defaults: ${summariseModels(summary.defaults)}`))
}

function switchSettingsPanel(panelName) {
  const navItems = document.querySelectorAll(".settings-nav-item")
  const panels = document.querySelectorAll(".settings-content-panel")
  navItems.forEach(item => {
    item.classList.toggle("is-active", item.dataset.panel === panelName)
  })
  panels.forEach(panel => {
    const expected = `settingsPanel${panelName.charAt(0).toUpperCase()}${panelName.slice(1)}`
    panel.classList.toggle("is-active", panel.id === expected)
  })
}

globalSettingsClose.addEventListener("click", hideGlobalSettings)
globalSettingsBackdrop.addEventListener("click", (event) => {
  if (event.target === globalSettingsBackdrop) hideGlobalSettings()
})
globalSettingsBackdrop.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return
  event.preventDefault()
  event.stopPropagation()
  hideGlobalSettings()
})

document.querySelectorAll(".settings-nav-item").forEach(item => {
  item.addEventListener("click", () => switchSettingsPanel(item.dataset.panel))
})

settingsGearButton.addEventListener("click", showGlobalSettings)

// Mark config as stale when window regains focus (user may have changed
// provider/models in terminal)
window.addEventListener("focus", () => {
  configInfoStale = true
})

withSessionSplash(() => window.kvist.bootstrap()).then((state) => {
  updateNavigation(state)
  protocolLabel.textContent = `v${state.protocolVersion}`
  const project = currentProject()
  displaySession(sessionItem(project, project?.status?.active_session))
  renderNavigation()
  promptInput.focus()
  if (state.initialGoal) void startGoal(state.initialGoal)
  const panel = new URLSearchParams(location.search).get("panel")
  if (panel === "settings") void showSettings()
  else if (panel === "palette") showPalette()
})
