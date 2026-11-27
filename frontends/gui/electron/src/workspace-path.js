"use strict"

const fs = require("node:fs")
const path = require("node:path")

// The core's system folder (kernel/config/system_workspace.py): the core recognises it by
// this place and treats it as help with the computer, not as a project.
function systemWorkspacePath(homePath, env) {
  const configDir = (env.KVIST_CONFIG_DIR || "").trim()
  if (configDir) {
    return path.resolve(configDir, "workspace")
  }
  if (typeof homePath !== "string" || !homePath.trim()) {
    throw new Error("Electron did not provide a home directory.")
  }
  return path.resolve(homePath, ".kvist", "workspace")
}

function ensureDefaultWorkspace(homePath, env = process.env) {
  const workspace = systemWorkspacePath(homePath, env)
  fs.mkdirSync(workspace, { recursive: true })
  return workspace
}

module.exports = { ensureDefaultWorkspace }
