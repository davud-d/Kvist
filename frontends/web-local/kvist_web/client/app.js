import { api } from "/assets/utils/api.js"
import { state, update } from "/assets/state/store.js"
import { renderNavigation } from "/assets/features/navigation.js"
import { applyLive, isCurrentSelection, loadDiff, openSession, recoverSelected, renderInspector, selectionSnapshot, setAgentState } from "/assets/features/session.js"
import { closePalette, openPalette } from "/assets/features/palette.js"

const startup = document.querySelector("#startup"), stage = document.querySelector("#startup-stage"), app = document.querySelector("#app")
const form = document.querySelector("#composer"), prompt = document.querySelector("#prompt"), modal = document.querySelector("#modal")
const NAVIGATION_EVENT_TYPES = new Set(["turn_end", "error", "input_request", "session_title", "agent.state_changed", "agent.process_exited", "connection.state_changed", "connection.lost", "connection.restored"])
let navigationGeneration = 0
let eventSource = null
let eventSourceGeneration = 0
let eventProbeTimer = null
let eventProbeAttempts = 0

function toast(message, error = false) {
  const node = document.createElement("div"); node.className = `toast ${error ? "error" : ""}`; node.textContent = message
  document.querySelector("#toasts").append(node); setTimeout(() => node.remove(), 5000)
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char])
}

async function refresh() {
  const generation = ++navigationGeneration
  const navigation = await api.navigation()
  if (generation !== navigationGeneration) return false
  update(current => { current.navigation = navigation })
  renderNavigation(selectSession)
  return true
}

async function selectSession(serverId, workspace, session) {
  try {
    if (await openSession(serverId, workspace, session)) renderNavigation(selectSession)
  } catch (error) {
    toast(error.message, true)
  }
}

async function bootstrap() {
  try {
    stage.textContent = "Restoring sessions"
    const data = await api.bootstrap()
    const stream = data.event_stream || { cursor: data.event_cursor || 0, epoch: "" }
    update(current => {
      current.navigation = data.navigation
      current.liveCursor = Number(stream.cursor) || 0
      current.liveEpoch = typeof stream.epoch === "string" ? stream.epoch : ""
    })
    renderNavigation(selectSession)
    stage.textContent = data.navigation.servers.some(item => item.connection.state === "connecting") ? "Connecting servers" : "Ready"
    startup.hidden = true; app.hidden = false; connectEvents()
  } catch (error) {
    stage.textContent = error.message; stage.style.color = "var(--red)"
  }
}

function clearEventProbe() {
  clearTimeout(eventProbeTimer)
  eventProbeTimer = null
  eventProbeAttempts = 0
}

function probeBrowserSession(source, generation) {
  if (eventProbeTimer || eventProbeAttempts >= 3 || source !== eventSource || generation !== eventSourceGeneration) return
  const delays = [250, 750, 1500]
  const delay = delays[eventProbeAttempts++]
  eventProbeTimer = setTimeout(async () => {
    eventProbeTimer = null
    if (source !== eventSource || generation !== eventSourceGeneration) return
    try {
      await api.bootstrap()
    } catch (error) {
      if (error?.status === 401) {
        source.close()
        if (eventSource === source) eventSource = null
        window.location.reload()
      }
    }
  }, delay)
}

function resetLiveStream(source, generation, envelope) {
  const event = envelope.event || {}
  // A remote bridge reset is forwarded through the local stream. It needs
  // durable recovery, but it must not replace the local controller epoch.
  if (event.remote !== true) {
    const cursor = Number(envelope.id)
    state.liveCursor = Number.isFinite(cursor) && cursor >= 0 ? cursor : 0
    state.liveEpoch = typeof event.epoch === "string" ? event.epoch : ""
    clearEventProbe()
    source.close()
    if (eventSource === source && generation === eventSourceGeneration) {
      eventSource = null
      queueMicrotask(() => {
        if (generation === eventSourceGeneration) connectEvents()
      })
    }
  }
  recoverSelected().catch(() => {})
  refresh().catch(() => {})
}

function connectEvents() {
  const generation = ++eventSourceGeneration
  if (eventSource) eventSource.close()
  const source = api.events(state.liveCursor, state.liveEpoch)
  eventSource = source
  source.onopen = () => {
    if (source !== eventSource || generation !== eventSourceGeneration) return
    clearEventProbe()
    document.querySelector("#connection-dot").className = "connection-dot"
    recoverSelected().catch(() => {})
    refresh().catch(() => {})
  }
  source.onmessage = message => {
    if (source !== eventSource || generation !== eventSourceGeneration) return
    let envelope
    try {
      envelope = JSON.parse(message.data)
    } catch (_) {
      toast("Kvist sent an invalid live event.", true)
      return
    }
    if (!envelope || typeof envelope !== "object" || !envelope.event || typeof envelope.event.type !== "string") return
    if (envelope.event.type === "stream.reset") {
      resetLiveStream(source, generation, envelope)
      return
    }
    const id = Number(envelope.id)
    if (Number.isFinite(id)) state.liveCursor = Math.max(state.liveCursor, id)
    applyLive(envelope)
    if (NAVIGATION_EVENT_TYPES.has(envelope.event.type)) refresh().catch(() => {})
  }
  source.onerror = () => {
    if (source !== eventSource || generation !== eventSourceGeneration) return
    document.querySelector("#connection-dot").className = "connection-dot reconnecting"
    probeBrowserSession(source, generation)
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault()
  const snapshot = selectionSnapshot()
  const text = prompt.value.trim()
  if (!snapshot || !text) return
  prompt.value = ""; prompt.style.height = "auto"
  try {
    await api.send(snapshot.selection, text)
    if (isCurrentSelection(snapshot)) setAgentState("running")
  } catch (error) {
    toast(error.message, true)
  }
})

prompt.addEventListener("input", () => { prompt.style.height = "auto"; prompt.style.height = `${Math.min(prompt.scrollHeight, 180)}px` })
prompt.addEventListener("keydown", event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); form.requestSubmit() } })

document.querySelector("#stop-agent").onclick = async () => {
  const snapshot = selectionSnapshot()
  if (!snapshot) return
  try {
    await api.stop(snapshot.selection)
    if (isCurrentSelection(snapshot)) setAgentState("stopped")
  } catch (error) {
    toast(error.message, true)
  }
}

document.querySelector("#toggle-inspector").onclick = event => {
  const panel = document.querySelector("#inspector"); panel.classList.toggle("closed")
  event.currentTarget.setAttribute("aria-expanded", String(!panel.classList.contains("closed")))
}

for (const tab of document.querySelectorAll(".inspector-tabs button")) tab.onclick = () => {
  for (const other of document.querySelectorAll(".inspector-tabs button")) other.setAttribute("aria-selected", "false")
  tab.setAttribute("aria-selected", "true"); state.inspectorTab = tab.dataset.tab; renderInspector()
  if (state.inspectorTab === "diff") loadDiff().catch(error => toast(error.message, true))
}

function paletteChoice(item) {
  if (item.selection?.session) selectSession(item.selection.serverId, item.selection.workspace, item.selection.session)
  else if (item.type === "command") command(item.label)
}

function command(label) {
  if (label === "New session") newSession()
  else if (label === "Show inbox") openPalette("inbox", paletteChoice)
  else if (label === "Connect server") showServerModal()
  else if (label === "Add workspace") addWorkspace()
  else if (label === "Stop agent") document.querySelector("#stop-agent").click()
  else if (label === "Settings") showConfigSync()
  else if (label.startsWith("Open ")) {
    const tab = label.slice(5).toLowerCase(); document.querySelector(`[data-tab="${tab}"]`)?.click()
  } else toast(`${label} is available from its dedicated panel.`)
}

document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openPalette("commands", paletteChoice) }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") { event.preventDefault(); openPalette("search", paletteChoice) }
  if (event.key === "Escape") { if (!modal.hidden) closeModal(); else closePalette() }
  if (event.key === "Tab") {
    const active = !modal.hidden ? modal : !document.querySelector("#palette").hidden ? document.querySelector("#palette") : null
    if (active) trapFocus(event, active)
  }
})

document.querySelector("#search-button").onclick = () => openPalette("search", paletteChoice)
document.querySelector("#server-switcher").onclick = () => openPalette("search", paletteChoice)
document.querySelector("#new-session").onclick = newSession

async function newSession() {
  let serverId = "local", workspace
  if (state.selected) { serverId = state.selected.serverId; workspace = state.selected.workspace }
  else {
    const first = state.navigation.servers.find(server => server.workspaces.length)
    serverId = first?.server.id; workspace = first?.workspaces[0]?.path
  }
  if (!workspace) return toast("Add a workspace first.", true)
  try {
    const value = await api.createSession({ serverId, workspace })
    await refresh()
    await selectSession(serverId, workspace, value.active_session)
  } catch (error) {
    toast(error.message, true)
  }
}

document.querySelector("#add-workspace").onclick = addWorkspace
async function addWorkspace() {
  const path = window.prompt("Absolute workspace path on the selected server")
  if (!path) return
  const serverId = state.selected?.serverId || "local"
  try {
    await api.addWorkspace(serverId, path); await refresh()
  } catch (error) {
    toast(error.message, true)
  }
}

document.querySelector("#connect-server").onclick = showServerModal
function showServerModal() {
  modal.hidden = false
  modal.innerHTML = `<form class="modal-card" role="dialog" aria-modal="true" aria-labelledby="server-dialog-title"><h2 id="server-dialog-title">Connect server</h2><div class="form-grid"><label>Name<input name="name" required></label><label>SSH alias<input name="alias" placeholder="server-gz8q"></label><label>Hostname<input name="host"></label><label>Port<input name="port" type="number" value="22"></label><label>Username<input name="username"></label><label>Authentication<select name="auth"><option value="ssh_config">SSH config / agent</option><option value="agent">SSH agent</option><option value="key">Private key</option><option value="password">Password</option></select></label><label>Private key<input name="key"></label><label>Password<input name="password" type="password"></label></div><div class="modal-actions"><button type="button" class="quiet-button" data-cancel>Cancel</button><button class="primary-button">Connect</button></div></form>`
  modal.querySelector("[data-cancel]").onclick = closeModal; modal.querySelector("form").onsubmit = submitServer
  queueMicrotask(() => modal.querySelector("input")?.focus())
}

async function submitServer(event) {
  event.preventDefault()
  const data = new FormData(event.currentTarget), name = data.get("name").trim(), id = name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-|-$/g, "")
  const payload = { id, name, kind: "remote", ssh_alias: data.get("alias"), host: data.get("host"), port: Number(data.get("port")), username: data.get("username"), auth_method: data.get("auth"), private_key_path: data.get("key"), password: data.get("password") }
  try {
    await api.addServer(payload)
    let result = await api.connectServer(id)
    if (result.state === "trust_required") {
      if (!confirm(`Trust ${result.host}?\n\n${result.key_type} ${result.fingerprint}\n\nVerify this fingerprint with the server administrator.`)) throw new Error("Host key was not trusted.")
      result = await api.connectServer(id, result.fingerprint)
    }
    if (result.state !== "connected") throw new Error(result.error || "Connection failed.")
    closeModal(); await refresh(); toast(`Connected to ${name}`)
  } catch (error) {
    toast(error.message, true)
  }
}

async function showConfigSync() {
  try {
    const preview = await api.configSyncPreview(), remotes = state.navigation.servers.filter(item => item.server.kind === "remote")
    if (!remotes.length) return toast("Connect a remote server first.", true)
    modal.hidden = false
    const options = remotes.map(item => `<option value="${esc(item.server.id)}">${esc(item.server.name)}</option>`).join("")
    const credentials = preview.credentials.map(item => `<label><input type="checkbox" name="credential" value="${esc(item.id)}" ${item.present ? "" : "disabled"}> ${esc(item.label)}</label>`).join("")
    modal.innerHTML = `<form class="modal-card" role="dialog" aria-modal="true" aria-labelledby="sync-dialog-title"><h2 id="sync-dialog-title">Sync configuration</h2><p>General Kvist settings are synchronized. Choose credentials explicitly.</p><label>Server<select name="server">${options}</select></label><div class="credential-list">${credentials || "No stored credentials found."}</div><div class="modal-actions"><button type="button" class="quiet-button" data-cancel>Cancel</button><button class="primary-button">Synchronize</button></div></form>`
    modal.querySelector("[data-cancel]").onclick = closeModal
    modal.querySelector("form").onsubmit = async submit => {
      submit.preventDefault()
      const data = new FormData(submit.currentTarget), credentials = data.getAll("credential")
      try {
        await api.syncConfig(data.get("server"), credentials); closeModal(); toast("Configuration synchronized.")
      } catch (error) {
        toast(error.message, true)
      }
    }
    queueMicrotask(() => modal.querySelector("select")?.focus())
  } catch (error) {
    toast(error.message, true)
  }
}

function closeModal() { modal.hidden = true; modal.replaceChildren() }
function trapFocus(event, container) {
  const focusable = [...container.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(node => node.offsetParent !== null)
  if (!focusable.length) return
  const first = focusable[0], last = focusable.at(-1)
  if (event.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || !container.contains(document.activeElement))) { event.preventDefault(); first.focus() }
}

document.querySelector("#inbox-button").onclick = () => openPalette("inbox", paletteChoice)
document.addEventListener("kvist:navigation-refresh", () => refresh().catch(() => {}))
bootstrap()
