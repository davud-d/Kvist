"use strict"

// Builds the Kvist core binary with PyInstaller into resources/bin, for the GUI to bundle.

const { spawnSync } = require("node:child_process")
const fs = require("node:fs")
const path = require("node:path")

// The repository's virtualenv when it has one (the layout differs per platform);
// otherwise the interpreter on PATH, which is what a CI runner or a fresh Mac has.
function findPython({ root, platform, env, existsSync }) {
  if (env.KVIST_PYTHON) return env.KVIST_PYTHON
  const venvPython = platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]
  for (const venv of [".venv", "venv"]) {
    const candidate = path.join(root, venv, ...venvPython)
    if (existsSync(candidate)) return candidate
  }
  return platform === "win32" ? "python" : "python3"
}

function main() {
  const guiDir = path.resolve(__dirname, "..")
  const root = path.resolve(guiDir, "..", "..", "..")
  const python = findPython({ root, platform: process.platform, env: process.env, existsSync: fs.existsSync })
  const result = spawnSync(
    python,
    [
      "-m", "PyInstaller", path.join(root, "kvist.spec"), "--noconfirm",
      "--distpath", path.join(guiDir, "resources", "bin"),
      "--workpath", path.join(guiDir, ".build", "core"),
    ],
    { cwd: guiDir, stdio: "inherit" },
  )
  if (result.error) {
    console.error(`could not run ${python}: ${result.error.message}`)
    process.exit(1)
  }
  process.exit(result.status ?? 1)
}

if (require.main === module) main()

module.exports = { findPython }
