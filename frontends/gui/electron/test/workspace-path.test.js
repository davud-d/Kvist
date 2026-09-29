"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")

const { ensureDefaultWorkspace } = require("../src/workspace-path")

test("creates the default Kvist workspace under Documents", (context) => {
  const documents = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-documents-"))
  context.after(() => fs.rmSync(documents, { recursive: true, force: true }))

  const workspace = ensureDefaultWorkspace(path.join(documents, "nested"))

  assert.equal(workspace, path.resolve(documents, "nested", "Kvist Workspace"))
  assert.equal(fs.statSync(workspace).isDirectory(), true)
})
