"use strict"

const fs = require("node:fs")
const path = require("node:path")

function resolveKvistCommand({
  appIsPackaged,
  resourcesPath,
  projectRoot,
  platform = process.platform,
  env = process.env,
  existsSync = fs.existsSync,
}) {
  const explicitBinary = env.KVIST_BINARY
  if (explicitBinary) {
    return { command: path.resolve(explicitBinary), prefixArgs: [] }
  }

  const binaryName = platform === "win32" ? "kvist.exe" : "kvist"
  const bundledBinary = path.join(resourcesPath, "bin", binaryName)
  if (appIsPackaged && existsSync(bundledBinary)) {
    return { command: bundledBinary, prefixArgs: [] }
  }

  // Some distro Electron launchers (notably Arch's electron43 package) report
  // app.isPackaged even for `electron .`. Detect a source checkout by main.py
  // before treating the missing bundled executable as a broken installation.
  const mainPath = path.join(projectRoot, "main.py")
  if (existsSync(mainPath)) {
    const venvPython = platform === "win32"
      ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
      : path.join(projectRoot, ".venv", "bin", "python")
    const command = existsSync(venvPython)
      ? venvPython
      : env.KVIST_PYTHON || (platform === "win32" ? "python" : "python3")
    return { command, prefixArgs: [mainPath] }
  }

  if (appIsPackaged) {
    throw new Error(`Bundled Kvist executable not found: ${bundledBinary}`)
  }
  throw new Error(`Kvist source entry point not found: ${mainPath}`)
}

module.exports = { resolveKvistCommand }
