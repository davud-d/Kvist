"use strict"

const assert = require("node:assert/strict")
const test = require("node:test")

const { ProtocolState } = require("../src/protocol-state")

test("host and network approvals keep their details and independent answers", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  for (const [requestId, title, approved] of [["host", "Host execution", true], ["network", "Network access", false]]) {
    const event = {
      type: "input_request", request_id: requestId, kind: "confirm", title,
      body: 'Argv: ["python", "build.py"]\nDirectory: project\nReason: build',
      remember_pattern: null,
    }
    assert.deepEqual(protocol.consume(event), { kind: "input_request", event })
    assert.deepEqual(protocol.createResponse(requestId, approved), { request_id: requestId, approved })
    assert.throws(() => protocol.createResponse(requestId, true), /no longer pending/)
  }
})

test("requires hello as the first event", () => {
  const protocol = new ProtocolState()
  const result = protocol.consume({ type: "status" })
  assert.equal(result.kind, "protocol_error")
})

test("rejects an unsupported protocol version", () => {
  const protocol = new ProtocolState()
  const result = protocol.consume({ type: "hello", protocol_version: 99 })
  assert.equal(result.kind, "protocol_error")
  assert.match(result.message, /expected 1/)
})

test("treats stream chunks as cumulative snapshots", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  protocol.consume({ type: "stream_start", stream_id: "s1" })

  const first = protocol.consume({ type: "stream_chunk", stream_id: "s1", text: "Hel" })
  const second = protocol.consume({ type: "stream_chunk", stream_id: "s1", text: "Hello" })

  assert.equal(first.text, "Hel")
  assert.equal(second.text, "Hello")
  assert.equal(protocol.streams.get("s1"), "Hello")
})

test("folds reasoning chunks and commit into snapshots", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  protocol.consume({ type: "stream_start", stream_id: "s1" })

  const start = protocol.consume({ type: "reasoning_start", stream_id: "s1" })
  const chunk = protocol.consume({ type: "reasoning_chunk", stream_id: "s1", text: "thinking" })
  const commit = protocol.consume({
    type: "reasoning_commit",
    stream_id: "s1",
    duration_seconds: 4.2,
  })

  assert.deepEqual(start, { kind: "reasoning_start", streamId: "s1" })
  assert.equal(chunk.kind, "reasoning_snapshot")
  assert.equal(chunk.text, "thinking")
  assert.equal(chunk.committed, false)
  assert.equal(commit.kind, "reasoning_snapshot")
  assert.equal(commit.committed, true)
  assert.equal(commit.durationSeconds, 4.2)
})

test("ignores unknown events after the handshake", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  assert.deepEqual(protocol.consume({ type: "future_event", value: 1 }), { kind: "ignore" })
})

test("preserves request_id and shapes each input response", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  protocol.consume({ type: "input_request", request_id: "choice-1", kind: "choice" })
  protocol.consume({ type: "input_request", request_id: "confirm-1", kind: "confirm" })
  protocol.consume({ type: "input_request", request_id: "text-1", kind: "text" })
  protocol.consume({ type: "input_request", request_id: "secret-1", kind: "secret" })

  assert.deepEqual(protocol.createResponse("choice-1", "openai"), {
    request_id: "choice-1",
    value: "openai",
  })
  assert.deepEqual(protocol.createResponse("confirm-1", false), {
    request_id: "confirm-1",
    approved: false,
  })
  assert.deepEqual(protocol.createResponse("text-1", "Kvist"), {
    request_id: "text-1",
    value: "Kvist",
  })
  assert.deepEqual(protocol.createResponse("secret-1", null), {
    request_id: "secret-1",
    value: null,
  })
})

test("passes background job events through to the renderer", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  for (const event of [
    { type: "job_started", job_id: "j1", command: ["sleep", "9"], purpose: "wait", call_id: "c" },
    { type: "job_output", job_id: "j1", output: "tick" },
    { type: "job_finished", job_id: "j1", status: "stopped", exit_code: null, output: "tick" },
    { type: "job_notice", text: "j1 finished", job_ids: ["j1"] },
  ]) {
    assert.deepEqual(protocol.consume(event), { kind: event.type, event })
  }
})

test("passes task state, screen, skill, helper and artifact events to the renderer", () => {
  const state = new ProtocolState()
  state.consume({ type: "hello", protocol_version: 1 })
  for (const type of ["task_state", "screen", "skill", "helper", "artifact"]) {
    const action = state.consume({ type, state: "running" })
    assert.equal(action.kind, type)
  }
})
