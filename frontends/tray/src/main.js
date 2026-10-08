"use strict"

const os = require("node:os")
const path = require("node:path")
const { execFile } = require("node:child_process")
const { promisify } = require("node:util")

const { app, BrowserWindow, Menu, Tray, ipcMain, nativeImage, screen } = require("electron")

const { locateBar, parseX11Dock, placePanel } = require("./bar-locator")
const { resolveCoreCommand, runCoreOp } = require("./core-command")
const { startEndpoint } = require("./endpoint")
const { focusTerminal } = require("./focus-window")
const { Registry } = require("./registry")
const { stringsFor } = require("./strings")
const { buildTranscript } = require("./transcript")

const execFileAsync = promisify(execFile)
const run = async (file, args) => (await execFileAsync(file, args)).stdout
const HISTORY_EVENTS = 300
const RUNNING_REFRESH_MS = 3000
const IDLE_EXIT_MS = 30 * 1000
// A click on the bar's icon moves focus away from the panel (hiding it) just before
// the toggle arrives (it is a fresh Python process, so well under a second later); the
// toggle then means "close", not "open again".
const REOPEN_GUARD_MS = 1500
const PANEL_SIZE = { width: 420, height: 580 }

const strings = stringsFor(app.getLocale())
// IPC cannot carry the tooltip formatters, only the plain texts.
const viewStrings = Object.fromEntries(Object.entries(strings).filter(([, value]) => typeof value === "string"))
const registry = new Registry()
let tray = null
let endpoint = null
let windowRef = null
let selectedId = null
let transcript = []
let notice = ""
let refreshTimer = null
let idleTimer = null
let core = null
let shownState = null
let hiddenByBlurAt = 0
// Where the bar's icon was when the pointer last stood on the bar: window managers warp the pointer
// when the panel takes or loses focus, so the pointer is not on the icon at every toggle.
let lastBarAnchor = null

// One icon per machine; a second start (another terminal) just exits.
if (!app.requestSingleInstanceLock()) app.exit(0)
// The process lives on its own: closing the chat window must not quit it.
app.on("window-all-closed", () => {})

function icon(state) {
  return nativeImage.createFromPath(path.join(__dirname, "..", "assets", `tray-${state}.png`)).resize({ width: 22, height: 22 })
}

function describeProblem(label, error) {
  notice = `${label}: ${error.message}`
  pushView()
}

function summary() {
  const cores = registry.list()
  const finished = cores.filter((entry) => entry.state === "done" && !entry.seen).length
  const tooltip = finished
    ? strings.tooltipDone(finished)
    : cores.length ? strings.tooltipSessions(cores.length) : strings.tooltipIdle
  return { state: registry.summary(), count: cores.length, tooltip }
}

function updateTray() {
  if (!tray) return
  const { state, tooltip } = summary()
  tray.setImage(icon(state))
  tray.setToolTip(tooltip)
}

function view() {
  const selected = registry.get(selectedId)
  return {
    strings: viewStrings,
    cores: registry.list().map((entry) => ({
      id: entry.id,
      title: path.basename(entry.workspace) || entry.workspace,
      session: entry.session,
      state: entry.state,
      unseen: entry.state === "done" && !entry.seen,
    })),
    selectedId: selected ? selected.id : null,
    ended: Boolean(selectedId && !selected),
    transcript: selected ? transcript : [],
    notice,
  }
}

function pushView() {
  if (windowRef && !windowRef.isDestroyed()) windowRef.webContents.send("tray:view", view())
}

async function loadTranscript() {
  const selected = registry.get(selectedId)
  if (!selected) return
  try {
    const payload = await runCoreOp(core, [
      "--workspace", selected.workspace,
      "--session-op", "history",
      "--session-name", selected.session,
      "--history-limit", String(HISTORY_EVENTS),
    ])
    transcript = buildTranscript(payload.events)
    notice = ""
  } catch (error) {
    notice = `${strings.historyFailed}: ${error.message}`
  }
  pushView()
}

function panelVisible() {
  return Boolean(windowRef && !windowRef.isDestroyed() && windowRef.isVisible())
}

function scheduleRefresh() {
  clearTimeout(refreshTimer)
  const selected = registry.get(selectedId)
  if (!selected || !panelVisible()) return
  refreshTimer = setTimeout(async () => {
    await loadTranscript()
    scheduleRefresh()
  }, selected.state === "running" ? RUNNING_REFRESH_MS : 60 * 1000)
}

async function select(id) {
  selectedId = id
  shownState = registry.get(id)?.state ?? null
  registry.markSeen(id)
  transcript = []
  pushView()
  await loadTranscript()
  scheduleRefresh()
}

/** The pointer is on the icon that was just clicked, so it says where the panel unfolds from. */
async function pointerPosition() {
  if (process.env.HYPRLAND_INSTANCE_SIGNATURE) {
    const [x, y] = (await run("hyprctl", ["cursorpos"])).split(",").map((value) => parseInt(value, 10))
    if (Number.isInteger(x) && Number.isInteger(y)) return { x, y }
  }
  return screen.getCursorScreenPoint()
}

async function hyprlandLayers() {
  if (!process.env.HYPRLAND_INSTANCE_SIGNATURE) return null
  return JSON.parse(await run("hyprctl", ["layers", "-j"]))
}

async function x11Dock(cursor, bounds) {
  if (process.env.HYPRLAND_INSTANCE_SIGNATURE || !process.env.DISPLAY) return null
  const pointer = (await run("xdotool", ["getmouselocation", "--shell"])).match(/^WINDOW=(\d+)/m)
  if (!pointer) return null
  const windowType = await run("xprop", ["-id", pointer[1], "_NET_WM_WINDOW_TYPE"])
  const geometry = await run("xdotool", ["getwindowgeometry", "--shell", pointer[1]])
  return parseX11Dock({ pointer: cursor, windowType, geometry, bounds })
}

async function panelBounds() {
  const pointer = await pointerPosition()
  const display = screen.getDisplayNearestPoint(pointer)
  let layers = null
  try {
    layers = await hyprlandLayers()
  } catch (error) {
    // hyprctl failed or printed something else: the dock and work-area lookups below still apply.
    describeProblem(strings.barLookupFailed, error)
  }
  let dock = null
  try {
    dock = await x11Dock(pointer, display.bounds)
  } catch (error) {
    // xdotool or xprop is missing: the work-area strip below still locates the bar where the window manager publishes it.
    describeProblem(strings.barLookupFailed, error)
  }
  const onBar = dock || locateBar({ bounds: display.bounds, workArea: display.workArea, cursor: pointer, layers })
  const onIcon = onBar && (dock || layers) ? { located: onBar, cursor: pointer, bounds: display.bounds } : null
  if (onIcon) lastBarAnchor = onIcon
  const anchor = onIcon || lastBarAnchor || { located: onBar, cursor: pointer, bounds: display.bounds }
  return placePanel({ ...anchor, size: PANEL_SIZE })
}

function ensureWindow() {
  if (windowRef && !windowRef.isDestroyed()) return windowRef
  windowRef = new BrowserWindow({
    ...PANEL_SIZE,
    show: false,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
    },
  })
  // Above fullscreen windows and on every workspace, like the bar it hangs from.
  windowRef.setAlwaysOnTop(true, "screen-saver")
  windowRef.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  windowRef.loadFile(path.join(__dirname, "..", "renderer", "index.html"))
  windowRef.on("blur", () => {
    hiddenByBlurAt = Date.now()
    windowRef.hide()
  })
  windowRef.on("hide", () => {
    clearTimeout(refreshTimer)
    watchIdle()
  })
  return windowRef
}

async function openPanel() {
  const target = registry.get(selectedId) || registry.list()[0]
  const chat = ensureWindow()
  const { origin, ...bounds } = await panelBounds()
  chat.setBounds(bounds)
  chat.show()
  chat.focus()
  chat.webContents.send("tray:open", origin)
  if (target) await select(target.id)
  else pushView()
}

async function togglePanel() {
  if (panelVisible()) {
    windowRef.hide()
    return
  }
  if (Date.now() - hiddenByBlurAt < REOPEN_GUARD_MS) return
  await openPanel()
}

async function disableIcon() {
  try {
    await runCoreOp(core, ["--tray-op", "disable"])
  } catch (error) {
    describeProblem(strings.disableFailed, error)
    return
  }
  app.quit()
}

function watchIdle() {
  clearTimeout(idleTimer)
  if (registry.list().length || panelVisible()) return
  idleTimer = setTimeout(() => app.quit(), IDLE_EXIT_MS)
}

ipcMain.on("tray:ready", pushView)
ipcMain.on("tray:select", (_event, id) => void select(id))
ipcMain.on("tray:disable", () => void disableIcon())
ipcMain.on("tray:hide", () => windowRef.hide())
ipcMain.on("tray:focus", async (_event, id) => {
  const target = registry.get(id)
  if (!target) return
  try {
    await focusTerminal({ pid: target.pid, run })
    windowRef.hide()
  } catch (error) {
    describeProblem(strings.focusFailed, error)
  }
})

function createTray() {
  tray = new Tray(icon("idle"))
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: strings.menuOpen, click: () => void togglePanel() },
      { label: strings.menuDisable, click: () => void disableIcon() },
      { type: "separator" },
      { label: strings.menuQuit, click: () => app.quit() },
    ]),
  )
  tray.on("click", () => void togglePanel())
}

app.whenReady().then(async () => {
  if (app.dock) app.dock.hide()
  core = resolveCoreCommand()
  const status = await runCoreOp(core, ["--tray-op", "status"])
  if (!status.enabled) {
    app.quit()
    return
  }
  endpoint = await startEndpoint({
    home: os.homedir(),
    registry,
    summary,
    onToggle: () => void togglePanel(),
    onProblem: (error) => console.error(error.message),
  })
  // A bar that carries the module already shows the icon; a second one in the tray would be a duplicate.
  if (!status.bars.length) createTray()
  registry.on("change", () => {
    updateTray()
    pushView()
    watchIdle()
    const selected = registry.get(selectedId)
    if (!selected || !panelVisible()) return
    // The user is looking at this session, so a finished turn needs no highlight.
    registry.markSeen(selected.id)
    if (selected.state === shownState) return
    shownState = selected.state
    void loadTranscript().then(scheduleRefresh)
  })
  updateTray()
  watchIdle()
  app.on("second-instance", () => void togglePanel())
})

app.on("will-quit", () => {
  if (endpoint) endpoint.stop()
})
