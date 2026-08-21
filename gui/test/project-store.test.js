"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")

const { ProjectStore } = require("../src/project-store")

test("project store remembers unique workspaces in recent order", (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-projects-"))
  context.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const store = new ProjectStore(path.join(directory, "projects.json"))
  const alpha = path.join(directory, "alpha")
  const beta = path.join(directory, "beta")

  store.remember(alpha, 10)
  store.remember(beta, 20)
  store.remember(alpha, 30)

  assert.deepEqual(store.load(), [
    { path: path.resolve(alpha), lastOpenedAt: 30 },
    { path: path.resolve(beta), lastOpenedAt: 20 },
  ])
})

test("project store ignores malformed records", (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-projects-"))
  context.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const storagePath = path.join(directory, "projects.json")
  fs.writeFileSync(storagePath, JSON.stringify({
    version: 1,
    projects: [null, { path: "" }, { path: directory, lastOpenedAt: "5" }],
  }))

  assert.deepEqual(new ProjectStore(storagePath).load(), [
    { path: path.resolve(directory), lastOpenedAt: 5 },
  ])
})
