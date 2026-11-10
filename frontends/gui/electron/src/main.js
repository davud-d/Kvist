"use strict"

const fs = require("node:fs")
const path = require("node:path")

const { app, BrowserWindow, Menu, dialog, ipcMain, nativeTheme, screen, shell } = require("electron")

const { resolveKvistCommand } = require("./command-resolver")
const { runCoreJson, runCoreOk } = require("./core-ops")
const { gitInfo, gitDiff, gitCommit, splitPatchByFile } = require("./git")
const { KvistProcess } = require("./kvist-process")
const { ProjectStore } = require("./project-store")
const { PROTOCOL_VERSION } = require("./protocol-state")
const { buildWindowOptions, applyMaximizeOnShow } = require("./window-config")
const { listWorkspaceFiles } = require("./workspace-files")
const { ensureDefaultWorkspace } = require("./workspace-path")

const BUSY_MESSAGE = "Finish or stop the current task first."
const SESSION_NAME = /^[A-Za-z0-9._-]{1,120}$/

let windowRef = null
let runner = null
let workspace = null
let lastDiagnostic = ""
let projectStore = null

function argumentValue(name) {
  const index = process.argv.indexOf(name)
  if (index === -1) return null
  return process.argv[index + 1] || null
}

function resolveCommand() {
  return resolveKvistCommand({
    appIsPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    projectRoot: path.resolve(__dirname, "../../../.."),
  })
}

function validateWorkspace(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("Choose a project before starting Kvist.")
  }
  const resolved = path.resolve(value)
  let stats
  try {
    stats = fs.statSync(resolved)
  } catch {
    throw new Error(`Project folder does not exist: ${resolved}`)
  }
  if (!stats.isDirectory()) throw new Error("The selected project is not a folder.")
  return resolved
}

function rememberWorkspace(value) {
  const resolved = validateWorkspace(value)
  projectStore.remember(resolved)
  return resolved
}

function requireIdle() {
  if (runner) throw new Error(BUSY_MESSAGE)
}

function checkedSessionName(name) {
  if (!SESSION_NAME.test(String(name || ""))) throw new Error("Not a session name.")
  return String(name)
}

// --- one-shot core operations -----------------------------------------------

function sessionOp(projectPath, op, extraArgs = []) {
  const resolved = validateWorkspace(projectPath)
  return runCoreOk(
    resolveCommand(),
    ["--workspace", resolved, "--session-op", op, ...extraArgs],
    { cwd: resolved, failure: "Kvist session process failed." },
  )
}

function coreOp(flag, op, { extraArgs = [], payload = null, timeout, failure }) {
  return runCoreOk(
    resolveCommand(),
    ["--workspace", workspace, flag, op, ...extraArgs],
    {
      cwd: workspace,
      stdin: payload === null ? "" : JSON.stringify(payload),
      timeout,
      failure,
    },
  )
}

const configInfo = () => runCoreOk(
  resolveCommand(),
  ["--workspace", workspace, "--config-info"],
  { cwd: workspace, failure: "Kvist could not read its configuration." },
)

// The payload can hold an API key, so it goes in on stdin: never argv.
const configOp = (op, payload) => coreOp("--config-op", op, {
  payload: payload ?? {},
  timeout: op === "chatgpt-login" ? 190000 : 30000,
  failure: "Kvist could not update its configuration.",
})

const journalOp = (op) => coreOp("--journal-op", op, {
  failure: "Kvist could not read its change journal.",
})

const listOp = (flag, extraArgs = []) => coreOp(flag, "list", {
  extraArgs: [...extraArgs, "--json"],
  failure: "Kvist could not read that list.",
})

async function projectSnapshot(project) {
  try {
    const status = await sessionOp(project.path, "list")
    return { ...project, available: true, status }
  } catch (error) {
    return { ...project, available: false, error: error.message, status: null }
  }
}

async function navigationSnapshot() {
  const stored = projectStore.load()
  const projects = new Array(stored.length)
  let nextIndex = 0
  async function worker() {
    while (nextIndex < stored.length) {
      const index = nextIndex
      nextIndex += 1
      projects[index] = await projectSnapshot(stored[index])
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, stored.length) }, worker))
  return { workspace, projects }
}

async function resolveInitialWorkspace() {
  const requested = argumentValue("--workspace")
  if (requested) {
    try {
      return validateWorkspace(requested)
    } catch {
      // An invalid explicit path falls through to the remembered projects.
    }
  } else {
    for (const project of projectStore.load()) {
      try {
        return validateWorkspace(project.path)
      } catch {
        // A missing project stays in the sidebar but is not activated.
      }
    }
  }
  return ensureDefaultWorkspace(app.getPath("documents"))
}

// --- the running task -----------------------------------------------------------

function send(channel, payload) {
  if (!windowRef || windowRef.isDestroyed()) return
  windowRef.webContents.send(channel, payload)
}

async function stopRunner() {
  const active = runner
  if (!active) return
  active.failClosedPending()
  await active.stop()
  if (runner === active) runner = null
}

function attachRunner(active) {
  active.on("event", (event) => send("kvist:event", event))
  active.on("diagnostic", (diagnostic) => { lastDiagnostic = diagnostic })
  active.on("protocol-error", (error) => send("kvist:protocol-error", { message: error.message }))
  active.on("process-error", (error) => send("kvist:process-error", { message: error.message }))
  active.on("exit", ({ code, signal, expected }) => {
    if (!expected && code !== 0) {
      send("kvist:process-error", {
        message: lastDiagnostic.trim() || `Kvist exited with code ${code ?? "unknown"}.`,
      })
    }
    send("kvist:exit", { code, signal, expected })
    if (runner === active) runner = null
  })
}

function launchRunner(launch) {
  const command = resolveCommand()
  lastDiagnostic = ""
  const active = new KvistProcess({
    workspace,
    command: command.command,
    prefixArgs: command.prefixArgs,
  })
  runner = active
  attachRunner(active)
  launch(active)
  return { ok: true }
}

function control(type, extra) {
  if (!runner) return { ok: false }
  runner.control(type, extra)
  return { ok: true }
}

// --- IPC --------------------------------------------------------------------------

function registerSessionIpc() {
  ipcMain.handle("kvist:bootstrap", async () => ({
    ...(await navigationSnapshot()),
    protocolVersion: PROTOCOL_VERSION,
    initialGoal: process.env.KVIST_INITIAL_GOAL || "",
  }))
  ipcMain.handle("kvist:navigation", () => navigationSnapshot())

  ipcMain.handle("kvist:choose-workspace", async () => {
    const result = await dialog.showOpenDialog(windowRef, {
      title: "Choose a project folder",
      defaultPath: workspace,
      properties: ["openDirectory", "createDirectory"],
    })
    if (result.canceled || !result.filePaths[0]) return null
    requireIdle()
    workspace = rememberWorkspace(result.filePaths[0])
    return workspace
  })

  ipcMain.handle("kvist:activate-project", async (_event, projectPath) => {
    requireIdle()
    workspace = rememberWorkspace(projectPath)
    return navigationSnapshot()
  })

  ipcMain.handle("kvist:remove-project", async (_event, projectPath) => {
    requireIdle()
    projectStore.forget(path.resolve(String(projectPath || "")))
    return navigationSnapshot()
  })

  ipcMain.handle("kvist:new-session", async (_event, projectPath) => {
    requireIdle()
    workspace = rememberWorkspace(projectPath || workspace)
    const status = await sessionOp(workspace, "new")
    return { name: status.active_session, navigation: await navigationSnapshot() }
  })

  ipcMain.handle("kvist:switch-session", async (_event, payload) => {
    requireIdle()
    workspace = rememberWorkspace(payload?.workspace)
    const status = await sessionOp(workspace, "switch", ["--session-name", checkedSessionName(payload?.name)])
    return { status, navigation: await navigationSnapshot() }
  })

  ipcMain.handle("kvist:delete-session", async (_event, payload) => {
    requireIdle()
    const resolved = rememberWorkspace(payload?.workspace || workspace)
    await sessionOp(resolved, "delete", ["--session-name", checkedSessionName(payload?.name)])
    return { navigation: await navigationSnapshot() }
  })

  // Reads one session's journal without switching to it.
  ipcMain.handle("kvist:session-history", (_event, payload) => {
    const extra = ["--session-name", checkedSessionName(payload?.name), "--history-limit", "1500"]
    if (Number.isInteger(payload?.before)) extra.push("--history-before", String(payload.before))
    return sessionOp(payload?.workspace || workspace, "history", extra)
  })

  ipcMain.handle("kvist:input-history", () => sessionOp(workspace, "input-history"))
  ipcMain.handle("kvist:input-history-add", (_event, text) => runCoreJson(
    resolveCommand(),
    ["--workspace", workspace, "--session-op", "input-history-add"],
    { cwd: workspace, stdin: String(text || ""), failure: "Could not save the input." },
  ))
}

function registerRunIpc() {
  ipcMain.handle("kvist:start", async (_event, payload) => {
    requireIdle()
    workspace = rememberWorkspace(payload?.workspace || workspace)
    return launchRunner((active) => active.start(payload?.goal || "", payload?.options || {}))
  })
  ipcMain.handle("kvist:resume", async (_event, payload) => {
    requireIdle()
    workspace = rememberWorkspace(payload?.workspace || workspace)
    return launchRunner((active) => active.resume(payload?.options || {}))
  })
  ipcMain.handle("kvist:respond", (_event, payload) => {
    if (!runner) throw new Error("Kvist is no longer running.")
    runner.respond(payload)
    return { ok: true }
  })
  ipcMain.handle("kvist:stop", async () => {
    await stopRunner()
    return { ok: true }
  })
  ipcMain.handle("kvist:pause", () => control("pause"))
  ipcMain.handle("kvist:takeover", () => control("takeover"))
  ipcMain.handle("kvist:stop-all", () => control("cancel"))
  ipcMain.handle("kvist:stop-helper", (_event, payload) => {
    const agentId = String(payload?.agentId || "")
    if (!/^h[0-9]{1,3}$/.test(agentId)) throw new Error("Unknown helper.")
    return control("helper_stop", { agent_id: agentId })
  })

  // The agent's screen viewer listens on 127.0.0.1 with its own token; only
  // such a link may be handed to the system browser from here.
  ipcMain.handle("kvist:open-viewer", (_event, payload) => {
    const url = String(payload?.url || "")
    if (!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}\/\?t=[A-Za-z0-9_-]+$/.test(url)) {
      throw new Error("Not a Kvist screen link.")
    }
    void shell.openExternal(url)
    return { ok: true }
  })

  ipcMain.handle("kvist:show-artifact", (_event, payload) => {
    const target = path.resolve(String(payload?.path || ""))
    const root = path.resolve(workspace || "", ".kvist", "artifacts")
    if (!workspace || !target.startsWith(root + path.sep)) throw new Error("Not a Kvist artifact.")
    shell.showItemInFolder(target)
    return { ok: true }
  })
}

function registerConfigIpc() {
  ipcMain.handle("kvist:config-info", () => configInfo())
  ipcMain.handle("kvist:add-custom-provider", (_event, payload) => {
    requireIdle()
    return configOp("add-custom-provider", payload)
  })
  ipcMain.handle("kvist:delete-custom-provider", (_event, payload) => {
    requireIdle()
    return configOp("delete-custom-provider", payload)
  })
  // Saves a provider and its API key (the key travels on stdin, see configOp).
  ipcMain.handle("kvist:set-provider", (_event, payload) => {
    requireIdle()
    return configOp("set-provider", payload)
  })
  ipcMain.handle("kvist:list-models", (_event, payload) => configOp("list-models", payload))
  ipcMain.handle("kvist:chatgpt-login", () => {
    requireIdle()
    return configOp("chatgpt-login", {})
  })
  ipcMain.handle("kvist:chatgpt-logout", () => {
    requireIdle()
    return configOp("chatgpt-logout", {})
  })
  ipcMain.handle("kvist:get-api-settings", () => configOp("get-api-settings", null))
  ipcMain.handle("kvist:update-api-settings", (_event, payload) => {
    requireIdle()
    return configOp("update-api-settings", payload)
  })
  ipcMain.handle("kvist:full-reset", () => {
    requireIdle()
    return configOp("full-reset", null)
  })
  ipcMain.handle("kvist:changes", () => journalOp("changes"))
  ipcMain.handle("kvist:revert", () => {
    requireIdle()
    return journalOp("revert")
  })
}

function registerCatalogIpc() {
  ipcMain.handle("kvist:skills", () => listOp("--skill-op"))
  ipcMain.handle("kvist:memory", async () => {
    const [status, list] = await Promise.all([
      coreOp("--memory-op", "status", { extraArgs: ["--json"], failure: "Could not read memory." }),
      coreOp("--memory-op", "list", { extraArgs: ["--json"], failure: "Could not read memory." }),
    ])
    return { status, records: list.records || [] }
  })
  ipcMain.handle("kvist:memory-forget", (_event, payload) => coreOp("--memory-op", "forget", {
    payload: { id: String(payload?.id || "") },
    extraArgs: ["--json"],
    failure: "Could not forget that memory.",
  }))
  ipcMain.handle("kvist:mcp", () => listOp("--mcp-op"))
  ipcMain.handle("kvist:connections", () => listOp("--connection-op"))
}

function registerWorkspaceIpc() {
  ipcMain.handle("kvist:git-info", () => gitInfo(workspace))
  ipcMain.handle("kvist:git-diff", () => gitDiff(workspace))
  ipcMain.handle("kvist:git-commit", (_event, payload) => {
    requireIdle()
    return gitCommit(workspace, payload)
  })
  ipcMain.handle("kvist:commit-message", async (_event, payload) => {
    const { patch } = await gitDiff(workspace)
    const wanted = new Set(Array.isArray(payload?.paths) ? payload.paths : [])
    const files = splitPatchByFile(patch).filter((file) => wanted.has(file.path))
    const result = await runCoreJson(
      resolveCommand(),
      ["--commit-message"],
      {
        cwd: workspace,
        stdin: JSON.stringify({ files, recent_subjects: payload?.recentSubjects || [] }),
        timeout: 90000,
        failure: "Kvist could not write a commit message.",
      },
    )
    if (!result.ok) throw new Error(result.error || "Kvist could not write a commit message.")
    return result
  })
  ipcMain.handle("kvist:list-files", (_event, query) => listWorkspaceFiles(workspace, query))
  ipcMain.handle("kvist:open-project-folder", async () => {
    const failure = await shell.openPath(workspace)
    if (failure) throw new Error(failure)
    return { ok: true }
  })
  ipcMain.handle("kvist:set-theme", (_event, theme) => {
    nativeTheme.themeSource = ["light", "dark"].includes(theme) ? theme : "system"
    return { ok: true }
  })
}

function createWindow() {
  // The default menu binds Ctrl+R / Ctrl+Shift+R to reloading the page, which
  // would drop a running task's screen. macOS needs it for copy and paste.
  if (process.platform !== "darwin") Menu.setApplicationMenu(null)
  const capture = Boolean(process.env.KVIST_CAPTURE_PATH)
  windowRef = new BrowserWindow({
    ...buildWindowOptions(screen),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  applyMaximizeOnShow(windowRef, { capture })

  windowRef.loadFile(path.join(__dirname, "../renderer/index.html"))
  windowRef.webContents.setWindowOpenHandler(({ url }) => {
    if (/^(https?:|mailto:)/i.test(url)) void shell.openExternal(url)
    return { action: "deny" }
  })
  // The page never navigates elsewhere; only a reload of itself (language change) is allowed.
  windowRef.webContents.on("will-navigate", (event, url) => {
    if (url !== windowRef.webContents.getURL()) event.preventDefault()
  })
  windowRef.webContents.on("render-process-gone", () => {
    if (runner) runner.failClosedPending()
    void stopRunner()
  })
  windowRef.on("closed", () => {
    windowRef = null
    void stopRunner()
  })

  const capturePath = process.env.KVIST_CAPTURE_PATH
  if (capturePath) {
    windowRef.webContents.once("did-finish-load", () => {
      setTimeout(async () => {
        const image = await windowRef.webContents.capturePage()
        await fs.promises.writeFile(path.resolve(capturePath), image.toPNG())
        app.quit()
      }, Number(process.env.KVIST_CAPTURE_DELAY || 3000))
    })
  }
}

app.whenReady().then(async () => {
  projectStore = new ProjectStore(path.join(app.getPath("userData"), "projects.json"))
  workspace = rememberWorkspace(await resolveInitialWorkspace())
  // A launch starts in its own session instead of reopening the previous
  // run's conversation. An interrupted task keeps its session so it stays
  // resumable, so a failure here must not block startup.
  try {
    await sessionOp(workspace, "launch")
  } catch (error) {
    lastDiagnostic = error.message
  }
  registerSessionIpc()
  registerRunIpc()
  registerConfigIpc()
  registerCatalogIpc()
  registerWorkspaceIpc()
  createWindow()
}).catch((error) => {
  dialog.showErrorBox("Kvist failed to start", error.message)
  app.quit()
})

app.on("before-quit", () => {
  if (runner) runner.failClosedPending()
  void stopRunner()
})

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit()
})

app.on("activate", () => {
  if (!BrowserWindow.getAllWindows().length) createWindow()
})
