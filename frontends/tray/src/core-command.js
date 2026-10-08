"use strict"

const fs = require("node:fs")
const path = require("node:path")
const { execFile } = require("node:child_process")

/**
 * The argv that starts the Kvist core: `KVIST_CORE_CMD` (a JSON array, set by the
 * core that started this daemon), else the source checkout's interpreter.
 */
function resolveCoreCommand({
  env = process.env,
  projectRoot = path.resolve(__dirname, "../../.."),
  platform = process.platform,
  existsSync = fs.existsSync,
} = {}) {
  if (env.KVIST_CORE_CMD) {
    const parts = JSON.parse(env.KVIST_CORE_CMD)
    if (!Array.isArray(parts) || !parts.length || !parts.every((part) => typeof part === "string")) {
      throw new Error("KVIST_CORE_CMD must be a non-empty JSON array of strings.")
    }
    return parts
  }
  const entry = path.join(projectRoot, "kernel", "__main__.py")
  if (!existsSync(entry)) throw new Error(`Kvist source entry point not found: ${entry}`)
  const venvPython = platform === "win32"
    ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
    : path.join(projectRoot, ".venv", "bin", "python")
  const python = existsSync(venvPython) ? venvPython : platform === "win32" ? "python" : "python3"
  return [python, entry]
}

/** Run a one-shot core operation and parse the single JSON object it prints. */
function runCoreOp(core, args) {
  return new Promise((resolve, reject) => {
    execFile(core[0], [...core.slice(1), ...args], { maxBuffer: 32 * 1024 * 1024 }, (error, stdout) => {
      let payload
      try {
        payload = JSON.parse(stdout)
      } catch {
        // Not JSON: the process failed before printing a result, so report its own error.
        reject(error || new Error("Kvist printed no result."))
        return
      }
      if (payload.ok === false) reject(new Error(payload.error || "Kvist reported a failure."))
      else resolve(payload)
    })
  })
}

module.exports = { resolveCoreCommand, runCoreOp }
