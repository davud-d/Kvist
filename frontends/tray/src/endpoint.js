"use strict"

const crypto = require("node:crypto")
const fs = require("node:fs")
const net = require("node:net")
const path = require("node:path")
const readline = require("node:readline")

/**
 * Loopback TCP endpoint the cores and the status bar talk to (kernel/tray/tray_link.py,
 * kernel/tray/tray_bar_client.py). Discovery is `<home>/.kvist/tray.json` with
 * `{port, token, pid}`, mode 0600. A connection must open with a hello carrying
 * the token; anything else is closed. A hello with `role: "bar"` subscribes to
 * `summary()` pushes (sent now and on every registry change) and may send `toggle`.
 */
async function startEndpoint({ home, registry, summary, onToggle, onProblem }) {
  const token = crypto.randomBytes(24).toString("hex")
  const sockets = new Set()
  const subscribers = new Set()
  let nextId = 1
  const push = (socket) => {
    if (!socket.destroyed) socket.write(JSON.stringify({ type: "summary", ...summary() }) + "\n")
  }
  const pushAll = () => subscribers.forEach(push)
  registry.on("change", pushAll)

  const server = net.createServer((socket) => {
    const id = nextId++
    sockets.add(socket)
    let authorized = false
    const lines = readline.createInterface({ input: socket })
    lines.on("line", (line) => {
      try {
        const message = JSON.parse(line)
        if (!authorized) {
          if (!sameToken(message.token, token)) throw new Error("Rejected a tray connection with a wrong token.")
          authorized = true
          if (message.role === "bar") {
            subscribers.add(socket)
            push(socket)
          }
          return
        }
        if (message.type === "state") registry.update(id, message)
        else if (message.type === "toggle") onToggle()
      } catch (error) {
        onProblem(error)
        socket.destroy()
      }
    })
    socket.on("error", () => socket.destroy())
    socket.on("close", () => {
      sockets.delete(socket)
      subscribers.delete(socket)
      registry.drop(id)
    })
  })

  await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", resolve)
  })
  const port = server.address().port
  const file = path.join(home, ".kvist", "tray.json")
  writePrivate(file, JSON.stringify({ port, token, pid: process.pid }))

  return {
    port,
    file,
    stop() {
      registry.off("change", pushAll)
      for (const socket of sockets) socket.destroy()
      server.close()
      removeIfOurs(file, port)
    },
  }
}

function sameToken(given, expected) {
  if (typeof given !== "string" || given.length !== expected.length) return false
  return crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected))
}

function writePrivate(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.${process.pid}.tmp`
  fs.writeFileSync(temporary, content, { mode: 0o600 })
  fs.renameSync(temporary, file)
}

function removeIfOurs(file, port) {
  let current
  try {
    current = JSON.parse(fs.readFileSync(file, "utf8"))
  } catch (error) {
    // Already removed or replaced by a broken file: there is nothing of ours to delete.
    if (error.code === "ENOENT" || error instanceof SyntaxError) return
    throw error
  }
  if (current.port === port) fs.unlinkSync(file)
}

module.exports = { startEndpoint }
