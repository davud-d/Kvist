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
  // app.isPackaged even for `electron .`. Detect a source checkout by the core's
  // entry point before treating the missing bundled executable as a broken installation.
  const mainPath = path.join(projectRoot, "kernel", "__main__.py")
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

// The native session program answers `--session-op` without starting the core: about 2 ms
// instead of the frozen core unpacking itself on every call. It is optional; null means
// the core serves session operations, as it always did. KVIST_SESSION_BIN names a file or,
// when that file is missing, switches the program off (the same rule the core follows).
function resolveSessionCommand({
  appIsPackaged,
  resourcesPath,
  projectRoot,
  platform = process.platform,
  env = process.env,
  existsSync = fs.existsSync,
}) {
  const name = platform === "win32" ? "kvist-session.exe" : "kvist-session"
  const candidates = env.KVIST_SESSION_BIN
    ? [path.resolve(env.KVIST_SESSION_BIN)]
    : [
        ...(appIsPackaged ? [path.join(resourcesPath, "bin", name)] : []),
        path.join(projectRoot, "kernel", "session-rs", "target", "release", name),
      ]
  const found = candidates.find((candidate) => existsSync(candidate))
  return found ? { command: found, prefixArgs: [] } : null
}

module.exports = { resolveKvistCommand, resolveSessionCommand }
