"use strict"

// Builds the native session program (kernel/session-rs) into resources/bin, next to the core,
// for the GUI to bundle. It is optional: without Rust the build goes on and the packaged
// app serves session operations through the core, which is slower and otherwise the same.

const { spawnSync } = require("node:child_process")
const fs = require("node:fs")
const path = require("node:path")

const programName = (platform) => (platform === "win32" ? "kvist-session.exe" : "kvist-session")

// `cargo` on PATH, or where rustup puts it before a new shell has it on PATH.
function findCargo({ env, platform, existsSync, home }) {
  if (env.CARGO) return env.CARGO
  const name = platform === "win32" ? "cargo.exe" : "cargo"
  const cargoHome = env.CARGO_HOME || path.join(home, ".cargo")
  const installed = path.join(cargoHome, "bin", name)
  return existsSync(installed) ? installed : name
}

function main() {
  const guiDir = path.resolve(__dirname, "..")
  const crate = path.resolve(guiDir, "..", "..", "..", "kernel", "session-rs")
  const cargo = findCargo({ env: process.env, platform: process.platform, existsSync: fs.existsSync, home: require("node:os").homedir() })
  const built = spawnSync(cargo, ["build", "--release", "--locked"], { cwd: crate, stdio: "inherit" })
  const name = programName(process.platform)
  const source = path.join(crate, "target", "release", name)
  if (built.error || built.status !== 0 || !fs.existsSync(source)) {
    console.warn("The native session program was not built; the app will serve session operations through the core.")
    return
  }
  const target = path.join(guiDir, "resources", "bin")
  fs.mkdirSync(target, { recursive: true })
  fs.copyFileSync(source, path.join(target, name))
  console.log(`bundled ${name}`)
}

if (require.main === module) main()

module.exports = { findCargo, programName }
