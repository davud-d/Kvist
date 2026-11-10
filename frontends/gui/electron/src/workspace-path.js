"use strict"

const fs = require("node:fs")
const path = require("node:path")

function ensureDefaultWorkspace(documentsPath) {
  if (typeof documentsPath !== "string" || !documentsPath.trim()) {
    throw new Error("Electron did not provide a documents directory.")
  }
  const workspace = path.resolve(documentsPath, "Kvist Workspace")
  fs.mkdirSync(workspace, { recursive: true })
  return workspace
}

module.exports = { ensureDefaultWorkspace }
