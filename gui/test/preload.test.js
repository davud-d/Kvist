"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

test("exposes the IPC bridge when markdown dependencies are unavailable", async () => {
  const source = fs.readFileSync(path.join(__dirname, "../src/preload.js"), "utf8")
  let api
  const invocations = []
  const ipcRenderer = {
    invoke: (...args) => {
      invocations.push(args)
      return Promise.resolve({ ok: true })
    },
    on: () => {},
    removeListener: () => {},
  }
  const contextBridge = {
    exposeInMainWorld: (name, value) => {
      assert.equal(name, "kvist")
      api = value
    },
  }

  vm.runInNewContext(source, {
    require: (name) => {
      if (name === "electron") return { contextBridge, ipcRenderer }
      throw new Error(`missing ${name}`)
    },
    console: { warn: () => {} },
  })

  assert.equal(typeof api.bootstrap, "function")
  assert.equal(typeof api.start, "function")
  assert.equal(api.renderMarkdown("<script>x</script>\nnext"), "&lt;script&gt;x&lt;/script&gt;<br>next")

  await api.start("hello", "/workspace", { role: "auto" })
  assert.equal(invocations[0][0], "kvist:start")
  assert.equal(JSON.stringify(invocations[0][1]), JSON.stringify({
    goal: "hello",
    workspace: "/workspace",
    options: { role: "auto" },
  }))
})
