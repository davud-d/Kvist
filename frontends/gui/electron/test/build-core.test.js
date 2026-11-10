"use strict"

const assert = require("node:assert/strict")
const path = require("node:path")
const test = require("node:test")

const { findPython } = require("../scripts/build-core")

const find = (platform, existing, env = {}) =>
  findPython({ root: "/repo", platform, env, existsSync: (value) => existing.includes(value) })

test("uses the repository virtualenv on macOS and Linux", () => {
  const venv = path.join("/repo", ".venv", "bin", "python")
  assert.equal(find("darwin", [venv]), venv)
  assert.equal(find("linux", [venv]), venv)
})

test("uses the Scripts folder of the virtualenv on Windows", () => {
  const venv = path.join("/repo", ".venv", "Scripts", "python.exe")
  assert.equal(find("win32", [venv]), venv)
})

test("falls back to the interpreter on PATH when there is no virtualenv", () => {
  assert.equal(find("darwin", []), "python3")
  assert.equal(find("linux", []), "python3")
  assert.equal(find("win32", []), "python")
})

test("KVIST_PYTHON wins over everything", () => {
  const venv = path.join("/repo", ".venv", "bin", "python")
  assert.equal(find("darwin", [venv], { KVIST_PYTHON: "/opt/homebrew/bin/python3.12" }), "/opt/homebrew/bin/python3.12")
})
