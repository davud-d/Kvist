"use strict"

const fs = require("node:fs")
const path = require("node:path")
const { execFile, spawn } = require("node:child_process")
const { promisify } = require("node:util")

const { app, BrowserWindow, dialog, ipcMain, screen, shell } = require("electron")

const { resolveKvistCommand } = require("./command-resolver")
const { KvistProcess } = require("./kvist-process")
const { ProjectStore } = require("./project-store")
const { buildWindowOptions, applyMaximizeOnShow } = require("./window-config")
const { PROTOCOL_VERSION } = require("./protocol-state")
const { ensureDefaultWorkspace } = require("./workspace-path")

const execFileAsync = promisify(execFile)

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

function defaultWorkspace() {
  const requested = argumentValue("--workspace")
  if (requested) return path.resolve(requested)
  return app.isPackaged ? ensureDefaultWorkspace(app.getPath("documents")) : path.resolve(__dirname, "../../../..")
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
    throw new Error("Choose a workspace before starting Kvist.")
  }
  const resolved = path.resolve(value)
  let stats
  try {
    stats = fs.statSync(resolved)
  } catch {
    throw new Error(`Workspace does not exist: ${resolved}`)
  }
  if (!stats.isDirectory()) throw new Error("The selected workspace is not a directory.")
  return resolved
}

function rememberWorkspace(value) {
  const resolved = validateWorkspace(value)
  projectStore.remember(resolved)
  return resolved
}

async function runSessionOp(projectPath, op, name = "") {
  const resolved = validateWorkspace(projectPath)
  const command = resolveCommand()
  const args = [
    ...command.prefixArgs,
    "--workspace", resolved,
    "--session-op", op,
  ]
  if (name) args.push("--session-name", name)
  let stdout = ""
  try {
    const completed = await execFileAsync(command.command, args, {
      cwd: resolved,
      encoding: "utf8",
      timeout: 30000,
      maxBuffer: 4 * 1024 * 1024,
      windowsHide: true,
    })
    stdout = completed.stdout
  } catch (error) {
    stdout = typeof error.stdout === "string" ? error.stdout : ""
    if (!stdout.trim()) throw new Error("Kvist session process failed.")
  }
  const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)
  let payload
  try {
    payload = JSON.parse(line || "")
  } catch {
    throw new Error("Kvist returned an invalid session response.")
  }
  if (!payload.ok) throw new Error(payload.error || "Session operation failed.")
  return payload
}

async function runConfigInfo() {
  const command = resolveCommand()
  const args = [...command.prefixArgs, "--workspace", workspace, "--config-info"]
  let stdout = ""
  try {
    const completed = await execFileAsync(command.command, args, {
      cwd: workspace,
      encoding: "utf8",
      timeout: 30000,
      maxBuffer: 4 * 1024 * 1024,
      windowsHide: true,
    })
    stdout = completed.stdout
  } catch (error) {
    stdout = typeof error.stdout === "string" ? error.stdout : ""
    if (!stdout.trim()) throw new Error("Kvist could not read its configuration.")
  }
  const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)
  let payload
  try {
    payload = JSON.parse(line || "")
  } catch {
    throw new Error("Kvist returned an invalid configuration response.")
  }
  if (!payload.ok) throw new Error(payload.error || "Could not read configuration.")
  return payload
}

// The payload can hold an API key, so it goes in on stdin — never argv, which
// is world-readable via `ps` and `/proc`.
function runConfigOp(op, payload) {
  const command = resolveCommand()
  const args = [...command.prefixArgs, "--workspace", workspace, "--config-op", op]
  return new Promise((resolve, reject) => {
    const child = spawn(command.command, args, {
      cwd: workspace,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    })
    let stdout = ""
    const timer = setTimeout(() => child.kill("SIGKILL"), op === "chatgpt-login" ? 190000 : 30000)
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk) => { stdout += chunk })
    child.on("error", () => {
      clearTimeout(timer)
      reject(new Error("Kvist could not update its configuration."))
    })
    child.on("close", () => {
      clearTimeout(timer)
      const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)
      let result
      try {
        result = JSON.parse(line || "")
      } catch {
        reject(new Error("Kvist returned an invalid configuration response."))
        return
      }
      if (!result.ok) reject(new Error(result.error || "Configuration update failed."))
      else resolve(result)
    })
    child.stdin.end(JSON.stringify(payload ?? {}))
  })
}

async function runJournalOp(op) {
  const resolved = validateWorkspace(workspace)
  const command = resolveCommand()
  const args = [...command.prefixArgs, "--workspace", resolved, "--journal-op", op]
  let stdout = ""
  try {
    const completed = await execFileAsync(command.command, args, {
      cwd: resolved,
      encoding: "utf8",
      timeout: 30000,
      maxBuffer: 8 * 1024 * 1024,
      windowsHide: true,
    })
    stdout = completed.stdout
  } catch (error) {
    stdout = typeof error.stdout === "string" ? error.stdout : ""
    if (!stdout.trim()) throw new Error("Kvist could not read its change journal.")
  }
  const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)
  let payload
  try {
    payload = JSON.parse(line || "")
  } catch {
    throw new Error("Kvist returned an invalid change-journal response.")
  }
  if (!payload.ok) throw new Error(payload.error || "Change-journal operation failed.")
  return payload
}

async function projectSnapshot(project) {
  try {
    const status = await runSessionOp(project.path, "status")
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
      // Fall through to the workspace picker for an invalid explicit path.
    }
  } else {
    for (const project of projectStore.load()) {
      try {
        return validateWorkspace(project.path)
      } catch {
        // Keep missing projects in the sidebar, but do not activate one.
      }
    }
  }
  const fallback = defaultWorkspace()
  if (fallback) return validateWorkspace(fallback)
  const result = await dialog.showOpenDialog({
    title: "Choose Kvist workspace",
    defaultPath: app.getPath("documents"),
    properties: ["openDirectory", "createDirectory"],
  })
  if (result.canceled || !result.filePaths[0]) return null
  return validateWorkspace(result.filePaths[0])
}

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
  active.on("diagnostic", (diagnostic) => {
    lastDiagnostic = diagnostic
  })
  active.on("protocol-error", (error) => {
    send("kvist:protocol-error", { message: error.message })
  })
  active.on("process-error", (error) => {
    send("kvist:process-error", { message: error.message })
  })
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

function registerIpc() {
  ipcMain.handle("kvist:bootstrap", async () => ({
    ...(await navigationSnapshot()),
    protocolVersion: PROTOCOL_VERSION,
    initialGoal: process.env.KVIST_INITIAL_GOAL || "",
  }))

  ipcMain.handle("kvist:navigation", () => navigationSnapshot())

  ipcMain.handle("kvist:config-info", () => runConfigInfo())

  ipcMain.handle("kvist:add-custom-provider", async (_event, payload) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runConfigOp("add-custom-provider", payload)
  })

  ipcMain.handle("kvist:delete-custom-provider", async (_event, payload) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runConfigOp("delete-custom-provider", payload)
  })

  ipcMain.handle("kvist:chatgpt-login", async () => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runConfigOp("chatgpt-login", {})
  })
  ipcMain.handle("kvist:chatgpt-logout", async () => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runConfigOp("chatgpt-logout", {})
  })

  ipcMain.handle("kvist:changes", () => runJournalOp("changes"))

  ipcMain.handle("kvist:revert", async () => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runJournalOp("revert")
  })

  ipcMain.handle("kvist:get-api-settings", () => runConfigOp("get-api-settings", null))

  ipcMain.handle("kvist:update-api-settings", async (_event, payload) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runConfigOp("update-api-settings", payload)
  })

  ipcMain.handle("kvist:full-reset", async () => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    return runConfigOp("full-reset", null)
  })

  ipcMain.handle("kvist:delete-session", async (_event, payload) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    const resolved = rememberWorkspace(payload?.workspace || workspace)
    await runSessionOp(resolved, "delete", payload?.name || "")
    return { navigation: await navigationSnapshot() }
  })

  ipcMain.handle("kvist:choose-workspace", async () => {
    const result = await dialog.showOpenDialog(windowRef, {
      title: "Choose Kvist workspace",
      defaultPath: workspace,
      properties: ["openDirectory", "createDirectory"],
    })
    if (result.canceled || !result.filePaths[0]) return null
    workspace = rememberWorkspace(result.filePaths[0])
    return workspace
  })

  ipcMain.handle("kvist:activate-project", async (_event, projectPath) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    workspace = rememberWorkspace(projectPath)
    return navigationSnapshot()
  })

  ipcMain.handle("kvist:new-session", async (_event, projectPath) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    workspace = rememberWorkspace(projectPath || workspace)
    const status = await runSessionOp(workspace, "new")
    return { name: status.active_session, navigation: await navigationSnapshot() }
  })

  ipcMain.handle("kvist:switch-session", async (_event, payload) => {
    if (runner) throw new Error("Finish or interrupt the current task first.")
    workspace = rememberWorkspace(payload?.workspace)
    const status = await runSessionOp(workspace, "switch", payload?.name || "")
    return { status, navigation: await navigationSnapshot() }
  })

  ipcMain.handle("kvist:start", async (_event, payload) => {
    if (runner) throw new Error("A Kvist task is already running.")
    workspace = rememberWorkspace(payload?.workspace || workspace)
    lastDiagnostic = ""
    const command = resolveCommand()
    const active = new KvistProcess({
      workspace,
      command: command.command,
      prefixArgs: command.prefixArgs,
    })
    runner = active
    attachRunner(active)
    active.start(payload?.goal || "", payload?.options || {})
    return { ok: true }
  })

  ipcMain.handle("kvist:resume", async (_event, payload) => {
    if (runner) throw new Error("A Kvist task is already running.")
    workspace = rememberWorkspace(payload?.workspace || workspace)
    lastDiagnostic = ""
    const command = resolveCommand()
    const active = new KvistProcess({
      workspace,
      command: command.command,
      prefixArgs: command.prefixArgs,
    })
    runner = active
    attachRunner(active)
    active.resume(payload?.options || {})
    return { ok: true }
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

  ipcMain.handle("kvist:pause", () => {
    if (!runner) return { ok: false }
    runner.control("pause")
    return { ok: true }
  })

  ipcMain.handle("kvist:stop-all", () => {
    if (!runner) return { ok: false }
    runner.control("cancel")
    return { ok: true }
  })

  ipcMain.handle("kvist:stop-helper", (_event, payload) => {
    if (!runner) return { ok: false }
    const agentId = String(payload?.agentId || "")
    if (!/^h[0-9]{1,3}$/.test(agentId)) throw new Error("Unknown helper.")
    runner.control("helper_stop", { agent_id: agentId })
    return { ok: true }
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

  // A helper's service session is read here without switching the person's
  // conversation: --session-op history only reads its journal.
  ipcMain.handle("kvist:helper-history", async (_event, payload) => {
    const name = String(payload?.session || "")
    if (!/^helper-[A-Za-z0-9._-]{1,120}$/.test(name)) throw new Error("Not a helper session.")
    return runSessionOp(workspace, "history", name)
  })

  ipcMain.handle("kvist:show-artifact", (_event, payload) => {
    const target = path.resolve(String(payload?.path || ""))
    const root = path.resolve(workspace || "", ".kvist", "artifacts")
    if (!workspace || !target.startsWith(root + path.sep)) throw new Error("Not a Kvist artifact.")
    shell.showItemInFolder(target)
    return { ok: true }
  })
}

function createWindow() {
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

  const openPanel = process.env.KVIST_CAPTURE_PANEL
  windowRef.loadFile(
    path.join(__dirname, "../renderer/index.html"),
    openPanel ? { query: { panel: openPanel } } : undefined,
  )
  windowRef.webContents.setWindowOpenHandler(({ url }) => {
    if (/^(https?:|mailto:)/i.test(url)) void shell.openExternal(url)
    return { action: "deny" }
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
  workspace = await resolveInitialWorkspace()
  if (!workspace) {
    app.quit()
    return
  }
  workspace = rememberWorkspace(workspace)
  // A launch starts in its own session instead of reopening the previous
  // run's conversation. An interrupted task keeps its session so it stays
  // resumable, so a failure here must not block startup.
  try {
    await runSessionOp(workspace, "launch")
  } catch (error) {
    lastDiagnostic = error.message
  }
  registerIpc()
  createWindow()
}).catch((error) => {
  void dialog.showErrorBox("Kvist failed to start", error.message)
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
