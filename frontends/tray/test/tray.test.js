"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const net = require("node:net")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")

const { resolveCoreCommand } = require("../src/core-command")
const { startEndpoint } = require("../src/endpoint")
const { focusTerminal } = require("../src/focus-window")
const { Registry } = require("../src/registry")
const { buildTranscript } = require("../src/transcript")

const snapshot = (state, extra = {}) => ({ type: "state", pid: 10, workspace: "/w", session: "s", state, ...extra })

test("registry summary: unseen done beats running beats idle, and viewing clears done", () => {
  const registry = new Registry()
  assert.equal(registry.summary(), "idle")
  registry.update(1, snapshot("running"))
  assert.equal(registry.summary(), "running")
  registry.update(2, snapshot("done"))
  assert.equal(registry.summary(), "done")
  assert.equal(registry.list()[0].id, 2)
  registry.markSeen(2)
  assert.equal(registry.summary(), "running")
  registry.update(2, snapshot("done"))
  assert.equal(registry.summary(), "done")
  registry.drop(2)
  registry.drop(1)
  assert.equal(registry.summary(), "idle")
})

test("registry rejects malformed snapshots", () => {
  const registry = new Registry()
  assert.throws(() => registry.update(1, { pid: "x" }), /Invalid state snapshot/)
  assert.throws(() => registry.update(1, snapshot("exploded")), /Unknown core state/)
})

function connect(port, lines) {
  return new Promise((resolve) => {
    const socket = net.connect(port, "127.0.0.1", () => {
      socket.write(lines.map((line) => JSON.stringify(line) + "\n").join(""))
      resolve(socket)
    })
  })
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 100))

test("endpoint requires the token, records states and drops closed cores", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-tray-"))
  const registry = new Registry()
  const problems = []
  const endpoint = await startEndpoint({ home, registry, onProblem: (error) => problems.push(error.message) })
  try {
    const stored = JSON.parse(fs.readFileSync(endpoint.file, "utf8"))
    assert.equal(stored.port, endpoint.port)
    if (process.platform !== "win32") assert.equal(fs.statSync(endpoint.file).mode & 0o777, 0o600)

    const good = await connect(endpoint.port, [{ type: "hello", token: stored.token }, snapshot("running")])
    await settle()
    assert.equal(registry.summary(), "running")

    const bad = await connect(endpoint.port, [{ type: "hello", token: "nope" }, snapshot("done")])
    await settle()
    assert.equal(registry.list().length, 1)
    assert.match(problems[0], /wrong token/)
    bad.destroy()

    good.destroy()
    await settle()
    assert.equal(registry.list().length, 0)
  } finally {
    endpoint.stop()
  }
  assert.equal(fs.existsSync(endpoint.file), false)
})

function fakeRun(table) {
  const calls = []
  const run = async (file, args) => {
    calls.push([file, ...args])
    const key = [file, ...args].join(" ")
    const answer = Object.entries(table).find(([prefix]) => key.startsWith(prefix))
    if (!answer) throw new Error(`unexpected: ${key}`)
    return answer[1]
  }
  return { run, calls }
}

test("focus on Hyprland matches the nearest ancestor that owns a window", async () => {
  const { run, calls } = fakeRun({
    "ps -o ppid= -p 300": "200\n",
    "ps -o ppid= -p 200": "100\n",
    "ps -o ppid= -p 100": "1\n",
    "hyprctl clients -j": JSON.stringify([{ pid: 100, address: "0xabc" }, { pid: 999, address: "0x1" }]),
    "hyprctl dispatch": "ok",
  })
  const method = await focusTerminal({
    pid: 300, run, platform: "linux", env: { HYPRLAND_INSTANCE_SIGNATURE: "x" },
  })
  assert.equal(method, "hyprland")
  assert.deepEqual(calls.at(-1), ["hyprctl", "dispatch", "focuswindow", "address:0xabc"])
})

test("focus on i3 finds the window with xdotool and focuses it with i3-msg", async () => {
  const { run, calls } = fakeRun({
    "ps -o ppid= -p 300": "100\n",
    "ps -o ppid= -p 100": "1\n",
    "xdotool search --pid 300": "\n",
    "xdotool search --pid 100": "4194307\n",
    "i3-msg": "[]",
  })
  const method = await focusTerminal({ pid: 300, run, platform: "linux", env: { DISPLAY: ":0", I3SOCK: "/s" } })
  assert.equal(method, "i3")
  assert.deepEqual(calls.at(-1), ["i3-msg", "[id=4194307]", "focus"])
})

test("focus reports a missing window and an unsupported desktop", async () => {
  const { run } = fakeRun({ "ps -o ppid= -p 300": "1\n", "hyprctl clients -j": "[]" })
  await assert.rejects(
    focusTerminal({ pid: 300, run, platform: "linux", env: { HYPRLAND_INSTANCE_SIGNATURE: "x" } }),
    /Could not find the terminal window/,
  )
  await assert.rejects(focusTerminal({ pid: 300, run, platform: "linux", env: {} }), /not supported/)
})

test("focus on Windows walks the CIM parent table", async () => {
  const { run, calls } = fakeRun({
    "powershell -NoProfile -Command Get-CimInstance": "300 200\r\n200 100\r\n100 4\r\n",
    "powershell -NoProfile -Command Add-Type": "ok\r\n",
  })
  assert.equal(await focusTerminal({ pid: 300, run, platform: "win32", env: {} }), "windows")
  assert.match(calls.at(-1).at(-1), /@\(300,200,100\)/)
})

test("core command comes from KVIST_CORE_CMD, then from the source checkout", () => {
  assert.deepEqual(resolveCoreCommand({ env: { KVIST_CORE_CMD: '["kvist"]' } }), ["kvist"])
  assert.throws(() => resolveCoreCommand({ env: { KVIST_CORE_CMD: "[]" } }), /non-empty/)
  const existing = new Set(["/p/kernel/__main__.py", "/p/.venv/bin/python"])
  assert.deepEqual(
    resolveCoreCommand({ env: {}, projectRoot: "/p", platform: "linux", existsSync: (value) => existing.has(value) }),
    ["/p/.venv/bin/python", "/p/kernel/__main__.py"],
  )
})

test("transcript shows user, answers once, steps and failures, and sanitizes HTML", () => {
  const entry = (event) => ({ event })
  const items = buildTranscript([
    entry({ type: "user", text: "fix it" }),
    entry({ type: "reasoning_chunk", text: "hidden" }),
    entry({ type: "step", action_description: "reads a.py", action_type: "read_file", user_message: "Looking." }),
    entry({ type: "action_result", ok: false, summary: "boom" }),
    entry({ type: "stream_commit", text: "Done <script>alert(1)</script> **ok**" }),
    entry({ type: "final", message: "Done <script>alert(1)</script> **ok**" }),
    entry({ type: "turn_end" }),
  ])
  assert.deepEqual(items.map((item) => item.kind), ["user", "assistant", "step", "error", "assistant"])
  assert.equal(items[0].text, "fix it")
  assert.doesNotMatch(items[4].html, /<script/)
  assert.match(items[4].html, /<strong>ok<\/strong>/)
})

const { barFromHyprlandLayers, locateBar, parseX11Dock, placePanel } = require("../src/bar-locator")

const MONITOR = { x: 0, y: 0, width: 1920, height: 1080 }

test("a bar is the strip between a display's bounds and its work area", () => {
  const top = locateBar({ bounds: MONITOR, workArea: { x: 0, y: 26, width: 1920, height: 1054 }, cursor: { x: 100, y: 10 } })
  assert.deepEqual(top, { edge: "top", bar: { x: 0, y: 0, width: 1920, height: 26 } })
  const bottom = locateBar({ bounds: MONITOR, workArea: { x: 0, y: 0, width: 1920, height: 1040 }, cursor: { x: 0, y: 0 } })
  assert.deepEqual(bottom.bar, { x: 0, y: 1040, width: 1920, height: 40 })
  assert.equal(locateBar({ bounds: MONITOR, workArea: MONITOR, cursor: { x: 0, y: 0 } }), null)
  // A reserved area this large is a dock or a panel in another layout, not a bar.
  assert.equal(locateBar({ bounds: MONITOR, workArea: { x: 300, y: 0, width: 1620, height: 1080 }, cursor: { x: 0, y: 0 } }), null)
})

test("a Hyprland layer surface under the pointer is the bar", () => {
  const layers = {
    "DP-1": { levels: { 0: [], 1: [], 2: [{ x: 6, y: 6, w: 1908, h: 32, namespace: "waybar" }], 3: [] } },
  }
  assert.deepEqual(barFromHyprlandLayers(layers, { x: 300, y: 20 }, MONITOR), {
    edge: "top",
    bar: { x: 6, y: 6, width: 1908, height: 32 },
  })
  assert.equal(barFromHyprlandLayers(layers, { x: 300, y: 500 }, MONITOR), null)
})

test("the panel hangs from the bar under the clicked icon and stays on the display", () => {
  const located = { edge: "top", bar: { x: 0, y: 0, width: 1920, height: 26 } }
  const size = { width: 420, height: 580 }
  const panel = placePanel({ located, cursor: { x: 200, y: 12 }, bounds: MONITOR, size })
  assert.deepEqual({ x: panel.x, y: panel.y }, { x: 172, y: 26 })
  assert.deepEqual(panel.origin, { x: 28, y: 0 })
  const edge = placePanel({ located, cursor: { x: 1900, y: 12 }, bounds: MONITOR, size })
  assert.equal(edge.x, 1920 - 420 - 4)
  const bottomBar = { edge: "bottom", bar: { x: 0, y: 1040, width: 1920, height: 40 } }
  const above = placePanel({ located: bottomBar, cursor: { x: 200, y: 1060 }, bounds: MONITOR, size })
  assert.equal(above.y, 1040 - 580)
  assert.equal(above.origin.y, 580)
  const second = { x: 1920, y: 0, width: 1920, height: 1080 }
  const onSecond = placePanel({ located: { edge: "top", bar: { x: 1920, y: 0, width: 1920, height: 26 } }, cursor: { x: 1930, y: 5 }, bounds: second, size })
  assert.equal(onSecond.x, 1924)
  const none = placePanel({ located: null, cursor: { x: 500, y: 300 }, bounds: MONITOR, size })
  assert.equal(none.y, 0)
})

test("bar clients get the summary at once and on every change, and may toggle the panel", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-tray-"))
  const registry = new Registry()
  let toggles = 0
  const endpoint = await startEndpoint({
    home,
    registry,
    summary: () => ({ state: registry.summary(), count: registry.list().length, tooltip: "t" }),
    onToggle: () => { toggles += 1 },
    onProblem: () => {},
  })
  try {
    const { token } = JSON.parse(fs.readFileSync(endpoint.file, "utf8"))
    const received = []
    const bar = await connect(endpoint.port, [{ type: "hello", token, role: "bar" }])
    bar.on("data", (chunk) => received.push(...chunk.toString().trim().split("\n").map((line) => JSON.parse(line))))
    await settle()
    registry.update(1, snapshot("done"))
    bar.write(JSON.stringify({ type: "toggle" }) + "\n")
    await settle()
    assert.deepEqual(received.map((entry) => [entry.state, entry.count]), [["idle", 0], ["done", 1]])
    assert.equal(toggles, 1)
    bar.destroy()
    await settle()
    registry.update(2, snapshot("running"))
  } finally {
    endpoint.stop()
  }
})

test("an X11 dock window under the pointer is the bar; other windows are not", () => {
  const geometry = "WINDOW=8388609\nX=0\nY=0\nWIDTH=1920\nHEIGHT=26\nSCREEN=0\n"
  const dock = "_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_DOCK\n"
  assert.deepEqual(parseX11Dock({ pointer: { x: 91, y: 13 }, windowType: dock, geometry, bounds: MONITOR }), {
    edge: "top",
    bar: { x: 0, y: 0, width: 1920, height: 26 },
  })
  const normal = "_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_NORMAL\n"
  assert.equal(parseX11Dock({ pointer: { x: 91, y: 13 }, windowType: normal, geometry, bounds: MONITOR }), null)
  assert.equal(parseX11Dock({ pointer: { x: 91, y: 500 }, windowType: dock, geometry, bounds: MONITOR }), null)
  assert.equal(parseX11Dock({ pointer: { x: 91, y: 13 }, windowType: dock, geometry: "", bounds: MONITOR }), null)
})
