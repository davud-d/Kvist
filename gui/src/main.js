"use strict"

const fs = require("node:fs")
const path = require("node:path")

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron")

const { KvistProcess } = require("./kvist-process")
const { PROTOCOL_VERSION } = require("./protocol-state")

let windowRef = null
let runner = null
let workspace = null
let lastDiagnostic = ""

function argumentValue(name) {
  const index = process.argv.indexOf(name)
  if (index === -1) return null
  return process.argv[index + 1] || null
}

function defaultWorkspace() {
  const requested = argumentValue("--workspace")
  if (requested) return path.resolve(requested)
  return app.isPackaged ? app.getPath("documents") : path.resolve(__dirname, "../..")
}

function resolveCommand() {
  const explicitBinary = process.env.KVIST_BINARY
  if (explicitBinary) {
    return { command: path.resolve(explicitBinary), prefixArgs: [] }
  }

  if (app.isPackaged) {
    const binaryName = process.platform === "win32" ? "kvist.exe" : "kvist"
    return {
      command: path.join(process.resourcesPath, "bin", binaryName),
      prefixArgs: [],
    }
  }

  const projectRoot = path.resolve(__dirname, "../..")
  const venvPython = process.platform === "win32"
    ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
    : path.join(projectRoot, ".venv", "bin", "python")
  const command = fs.existsSync(venvPython)
    ? venvPython
    : process.env.KVIST_PYTHON || (process.platform === "win32" ? "python" : "python3")
  return { command, prefixArgs: [path.join(projectRoot, "main.py")] }
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

async function resolveInitialWorkspace() {
  try {
    return validateWorkspace(defaultWorkspace())
  } catch (error) {
    const result = await dialog.showOpenDialog({
      title: error.message,
      defaultPath: app.getPath("documents"),
      properties: ["openDirectory", "createDirectory"],
    })
    if (result.canceled || !result.filePaths[0]) return null
    return validateWorkspace(result.filePaths[0])
  }
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
  ipcMain.handle("kvist:bootstrap", () => ({
    workspace,
    protocolVersion: PROTOCOL_VERSION,
    initialGoal: process.env.KVIST_INITIAL_GOAL || "",
  }))

  ipcMain.handle("kvist:choose-workspace", async () => {
    const result = await dialog.showOpenDialog(windowRef, {
      title: "Choose Kvist workspace",
      defaultPath: workspace,
      properties: ["openDirectory", "createDirectory"],
    })
    if (result.canceled || !result.filePaths[0]) return null
    workspace = validateWorkspace(result.filePaths[0])
    return workspace
  })

  ipcMain.handle("kvist:start", async (_event, payload) => {
    if (runner) throw new Error("A Kvist task is already running.")
    workspace = validateWorkspace(payload?.workspace || workspace)
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
  workspace = await resolveInitialWorkspace()
  if (!workspace) {
    app.quit()
    return
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
