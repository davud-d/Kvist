"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")

const { ensureDefaultWorkspace } = require("../src/workspace-path")

function temporaryDirectory(context) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-home-"))
  context.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  return directory
}

test("creates the system folder under ~/.kvist", (context) => {
  const home = temporaryDirectory(context)

  const workspace = ensureDefaultWorkspace(home, {})

  assert.equal(workspace, path.resolve(home, ".kvist", "workspace"))
  assert.equal(fs.statSync(workspace).isDirectory(), true)
})

test("follows KVIST_CONFIG_DIR like the core does", (context) => {
  const config = temporaryDirectory(context)

  const workspace = ensureDefaultWorkspace("/nowhere", { KVIST_CONFIG_DIR: config })

  assert.equal(workspace, path.resolve(config, "workspace"))
})
