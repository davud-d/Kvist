"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")

const { lastJsonLine, runCoreJson, runCoreOk } = require("../src/core-ops")
const { listWorkspaceFiles, rank } = require("../src/workspace-files")

// A stand-in for the core: prints noise, echoes stdin back in its last JSON line.
function fakeCore(script) {
  return { command: process.execPath, prefixArgs: ["-e", script] }
}

test("the answer is the last JSON line, whatever came before", () => {
  assert.deepEqual(lastJsonLine('warning\n{"ok":false}\n{"ok":true,"n":1}\n'), { ok: true, n: 1 })
  assert.throws(() => lastJsonLine("no json"))
})

test("runCoreJson passes stdin through and parses the reply", async () => {
  const core = fakeCore('let d="";process.stdin.on("data",c=>d+=c).on("end",()=>console.log("noise\\n"+JSON.stringify({ok:true,echo:d})))')
  const result = await runCoreJson(core, [], { cwd: os.tmpdir(), stdin: '{"api_key":"k"}', failure: "x" })
  assert.deepEqual(result, { ok: true, echo: '{"api_key":"k"}' })
})

test("a refusal still resolves, and runCoreOk turns it into an error", async () => {
  const core = fakeCore('console.log(JSON.stringify({ok:false,error:"Unknown session"}));process.exit(1)')
  const refusal = await runCoreJson(core, [], { cwd: os.tmpdir(), failure: "x" })
  assert.equal(refusal.ok, false)
  await assert.rejects(runCoreOk(core, [], { cwd: os.tmpdir(), failure: "failed" }), /Unknown session/)
})

test("a core that prints no JSON rejects with its last stderr line", async () => {
  const core = fakeCore('console.error("first");console.error("Traceback: boom");process.exit(2)')
  await assert.rejects(runCoreJson(core, [], { cwd: os.tmpdir(), failure: "fallback" }), /Traceback: boom/)
})

test("a core that cannot start rejects with the given failure", async () => {
  await assert.rejects(
    runCoreJson({ command: "/nonexistent/kvist", prefixArgs: [] }, [], { cwd: os.tmpdir(), failure: "Could not start" }),
    /Could not start/,
  )
})

test("a core that never answers is killed at the timeout", async () => {
  const core = fakeCore("setInterval(()=>{},1000)")
  await assert.rejects(runCoreJson(core, [], { cwd: os.tmpdir(), timeout: 200, failure: "timed out" }), /timed out/)
})

test("mention ranking puts name matches before path matches", () => {
  const files = ["docs/readme-notes.md", "src/app.py", "app/main.py", "src/utils/apple.py"]
  assert.deepEqual(rank(files, "app"), ["src/app.py", "src/utils/apple.py", "app/main.py"])
  assert.deepEqual(rank(files, "zzz"), [])
  assert.equal(rank(files, "").length, 4)
})

test("without git the walk skips dependencies and Kvist's own folder", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-files-"))
  fs.mkdirSync(path.join(dir, "node_modules", "dep"), { recursive: true })
  fs.mkdirSync(path.join(dir, ".kvist"))
  fs.mkdirSync(path.join(dir, "src"))
  fs.writeFileSync(path.join(dir, "node_modules", "dep", "index.js"), "")
  fs.writeFileSync(path.join(dir, ".kvist", "x.json"), "")
  fs.writeFileSync(path.join(dir, "src", "main.py"), "")
  fs.writeFileSync(path.join(dir, "README.md"), "")
  assert.deepEqual((await listWorkspaceFiles(dir, "")).sort(), ["README.md", "src/main.py"])
  assert.deepEqual(await listWorkspaceFiles(dir, "main"), ["src/main.py"])
})
