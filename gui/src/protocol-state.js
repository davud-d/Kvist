(function exposeProtocolState(root, factory) {
  const api = factory()
  if (typeof module === "object" && module.exports) {
    module.exports = api
    return
  }
  root.KvistProtocol = api
})(typeof globalThis !== "undefined" ? globalThis : this, function buildProtocolState() {
  "use strict"

  const PROTOCOL_VERSION = 1
  const KNOWN_EVENTS = new Set([
    "hello",
    "live_start",
    "live_stop",
    "status",
    "step",
    "plan",
    "sandbox_notice",
    "stream_start",
    "stream_chunk",
    "stream_commit",
    "reasoning_start",
    "reasoning_chunk",
    "reasoning_commit",
    "message",
    "final",
    "debug",
    "error",
    "input_request",
  ])

  class ProtocolState {
    constructor(expectedVersion = PROTOCOL_VERSION) {
      this.expectedVersion = expectedVersion
      this.handshakeComplete = false
      this.streams = new Map()
      this.pendingRequests = new Map()
    }

    consume(event) {
      if (!event || typeof event !== "object" || typeof event.type !== "string") {
        return { kind: "protocol_error", message: "Kvist sent an invalid event." }
      }

      if (!this.handshakeComplete) {
        if (event.type !== "hello") {
          return { kind: "protocol_error", message: "The first Kvist event was not hello." }
        }
        if (event.protocol_version !== this.expectedVersion) {
          return {
            kind: "protocol_error",
            message: `Unsupported Kvist protocol ${event.protocol_version}; expected ${this.expectedVersion}.`,
          }
        }
        this.handshakeComplete = true
        return { kind: "hello", event }
      }

      if (!KNOWN_EVENTS.has(event.type)) {
        return { kind: "ignore" }
      }

      if (event.type === "stream_start") {
        this.streams.set(event.stream_id, "")
      }

      if (event.type === "stream_chunk" || event.type === "stream_commit") {
        const text = typeof event.text === "string" ? event.text : ""
        this.streams.set(event.stream_id, text)
        return {
          kind: "stream_snapshot",
          streamId: event.stream_id,
          text,
          committed: event.type === "stream_commit",
        }
      }

      if (event.type === "reasoning_start") {
        return { kind: "reasoning_start", streamId: event.stream_id }
      }

      if (event.type === "reasoning_chunk" || event.type === "reasoning_commit") {
        const committed = event.type === "reasoning_commit"
        return {
          kind: "reasoning_snapshot",
          streamId: event.stream_id,
          text: typeof event.text === "string" ? event.text : "",
          committed,
          durationSeconds:
            typeof event.duration_seconds === "number" ? event.duration_seconds : 0,
        }
      }

      if (event.type === "input_request") {
        this.pendingRequests.set(event.request_id, event)
      }

      return { kind: event.type, event }
    }

    createResponse(requestId, answer) {
      const request = this.pendingRequests.get(requestId)
      if (!request) {
        throw new Error("The input request is no longer pending.")
      }
      this.pendingRequests.delete(requestId)

      if (request.kind === "confirm") {
        return { request_id: requestId, approved: answer === true }
      }
      if (answer !== null && typeof answer !== "string") {
        throw new TypeError("Input responses must be a string or null.")
      }
      return { request_id: requestId, value: answer }
    }

    reset() {
      this.handshakeComplete = false
      this.streams.clear()
      this.pendingRequests.clear()
    }
  }

  return { KNOWN_EVENTS, PROTOCOL_VERSION, ProtocolState }
})
