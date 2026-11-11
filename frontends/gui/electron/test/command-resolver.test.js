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

test("finds the bundled binary inside a macOS app bundle", () => {
  const bundled = "/Applications/Kvist.app/Contents/Resources/bin/kvist"
  const command = resolveKvistCommand({
    appIsPackaged: true,
    resourcesPath: "/Applications/Kvist.app/Contents/Resources",
    projectRoot: "/Applications/Kvist.app/Contents/Resources/app.asar",
    platform: "darwin",
    env: {},
    existsSync: (value) => value === bundled,
  })

  assert.deepEqual(command, { command: bundled, prefixArgs: [] })
})

const { resolveSessionCommand } = require("../src/command-resolver")

const session = (existing, overrides = {}) => resolveSessionCommand({
  appIsPackaged: true,
  resourcesPath: "/app/resources",
  projectRoot: "/project",
  platform: "linux",
  env: {},
  existsSync: (value) => existing.includes(value),
  ...overrides,
})

test("a packaged app uses the session program bundled beside the core", () => {
  assert.deepEqual(session(["/app/resources/bin/kvist-session"]), { command: "/app/resources/bin/kvist-session", prefixArgs: [] })
  assert.equal(session(["/app/resources/bin/kvist-session.exe"]), null) // the Windows name means nothing on Linux
  assert.equal(
    session(["/app/resources/bin/kvist-session.exe"], { platform: "win32" }).command,
    "/app/resources/bin/kvist-session.exe",
  )
})

test("a source checkout uses the program built in the crate", () => {
  const built = "/project/kernel/session-rs/target/release/kvist-session"
  assert.equal(session([built], { appIsPackaged: false }).command, built)
  assert.equal(session([built], { appIsPackaged: false, platform: "win32" }), null)
})

test("without the program the core serves session operations", () => {
  assert.equal(session([]), null)
})

test("KVIST_SESSION_BIN names the program, and a missing file switches it off", () => {
  const present = ["/custom/kvist-session", "/app/resources/bin/kvist-session"]
  assert.equal(session(present, { env: { KVIST_SESSION_BIN: "/custom/kvist-session" } }).command, "/custom/kvist-session")
  assert.equal(session(present, { env: { KVIST_SESSION_BIN: "/custom/absent" } }), null)
})
