"use strict"

const fs = require("node:fs")
const path = require("node:path")
const { execFile } = require("node:child_process")
const { promisify } = require("node:util")

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron")

const { resolveKvistCommand } = require("./command-resolver")
const { KvistProcess } = require("./kvist-process")
const { ProjectStore } = require("./project-store")
const { PROTOCOL_VERSION } = require("./protocol-state")

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
  return app.isPackaged ? null : path.resolve(__dirname, "../..")
}

function resolveCommand() {
  return resolveKvistCommand({
    appIsPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    projectRoot: path.resolve(__dirname, "../.."),
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

function newSessionName(existingNames) {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)
  const base = `chat-${stamp}`
  if (!existingNames.has(base)) return base
  for (let suffix = 2; suffix < 1000; suffix += 1) {
    const candidate = `${base}-${suffix}`
    if (!existingNames.has(candidate)) return candidate
  }
  throw new Error("Could not allocate a new session name.")
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
  try {
    if (fallback) return validateWorkspace(fallback)
  } catch (error) {
    // Fall through to the workspace picker.
  }
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
    const status = await runSessionOp(workspace, "status")
    const name = newSessionName(new Set(status.sessions || []))
    await runSessionOp(workspace, "create", name)
    return { name, navigation: await navigationSnapshot() }
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

  ipcMain.handle("kvist:respond", (_event, payload) => {
    if (!runner) throw new Error("Kvist is no longer running.")
    runner.respond(payload)
    return { ok: true }
  })

  ipcMain.handle("kvist:stop", async () => {
    await stopRunner()
    return { ok: true }
  })
}

function createWindow() {
  windowRef = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 760,
    minHeight: 560,
    show: !process.env.KVIST_CAPTURE_PATH,
    backgroundColor: "#080d12",
    title: "Kvist",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })

  windowRef.loadFile(path.join(__dirname, "../renderer/index.html"))
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
      }, Number(process.env.KVIST_CAPTURE_DELAY || 1200))
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
