"use strict"

const assert = require("node:assert/strict")
const test = require("node:test")

const { resolveKvistCommand } = require("../src/command-resolver")

test("uses source checkout when distro Electron incorrectly reports packaged", () => {
  const existing = new Set([
    "/project/kernel/__main__.py",
    "/project/.venv/bin/python",
  ])
  const command = resolveKvistCommand({
    appIsPackaged: true,
    resourcesPath: "/usr/lib/electron43/resources",
    projectRoot: "/project",
    platform: "linux",
    env: {},
    existsSync: (value) => existing.has(value),
  })

  assert.deepEqual(command, {
    command: "/project/.venv/bin/python",
    prefixArgs: ["/project/kernel/__main__.py"],
  })
})

test("prefers a real bundled binary in a packaged application", () => {
  const bundled = "/app/resources/bin/kvist"
  const command = resolveKvistCommand({
    appIsPackaged: true,
    resourcesPath: "/app/resources",
    projectRoot: "/app/app.asar",
    platform: "linux",
    env: {},
    existsSync: (value) => value === bundled,
  })

  assert.deepEqual(command, { command: bundled, prefixArgs: [] })
})

test("reports a broken packaged installation when neither runtime exists", () => {
  assert.throws(
    () => resolveKvistCommand({
      appIsPackaged: true,
      resourcesPath: "/app/resources",
      projectRoot: "/app/app.asar",
      platform: "linux",
      env: {},
      existsSync: () => false,
    }),
    /Bundled Kvist executable not found/,
  )
})
