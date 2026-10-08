"use strict"

const { EventEmitter } = require("node:events")

const STATES = new Set(["running", "done"])

/**
 * Cores currently connected to the tray, keyed by connection id.
 * A core's "done" is unseen (green) until the user opens its chat.
 */
class Registry extends EventEmitter {
  constructor() {
    super()
    this.cores = new Map()
  }

  update(id, snapshot) {
    if (!snapshot || !Number.isInteger(snapshot.pid) || typeof snapshot.workspace !== "string") {
      throw new Error("Invalid state snapshot from a Kvist core.")
    }
    if (!STATES.has(snapshot.state)) {
      throw new Error(`Unknown core state: ${snapshot.state}`)
    }
    this.cores.set(id, {
      id,
      pid: snapshot.pid,
      workspace: snapshot.workspace,
      session: String(snapshot.session || ""),
      state: snapshot.state,
      seen: false,
      updatedAt: Date.now(),
    })
    this.emit("change")
  }

  drop(id) {
    if (this.cores.delete(id)) this.emit("change")
  }

  markSeen(id) {
    const core = this.cores.get(id)
    if (!core || core.seen) return
    core.seen = true
    this.emit("change")
  }

  get(id) {
    return this.cores.get(id) || null
  }

  /** Cores that finished and wait for the user, then running ones, then the rest. */
  list() {
    const rank = (core) => (core.state === "done" && !core.seen ? 0 : core.state === "running" ? 1 : 2)
    return [...this.cores.values()].sort((a, b) => rank(a) - rank(b) || b.updatedAt - a.updatedAt)
  }

  /** The icon state: "done" if any finished turn is unseen, "running" if any turn runs, else "idle". */
  summary() {
    const cores = [...this.cores.values()]
    if (cores.some((core) => core.state === "done" && !core.seen)) return "done"
    if (cores.some((core) => core.state === "running")) return "running"
    return "idle"
  }
}

module.exports = { Registry }
