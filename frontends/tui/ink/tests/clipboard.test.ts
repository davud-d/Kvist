import assert from "node:assert/strict"
import { test } from "node:test"

import { candidateTargets, copyToClipboard, oscCopySequence } from "../clipboard.js"

test("candidateTargets prefers Wayland over X11 on Linux, in order", () => {
  assert.deepEqual(candidateTargets("linux", { WAYLAND_DISPLAY: "wayland-0", DISPLAY: ":0" }), [
    { method: "wl-copy", command: "wl-copy", args: [] },
    { method: "xclip", command: "xclip", args: ["-selection", "clipboard"] },
  ])
})

test("candidateTargets picks only the session that is actually available", () => {
  assert.deepEqual(candidateTargets("linux", { DISPLAY: ":0" }), [
    { method: "xclip", command: "xclip", args: ["-selection", "clipboard"] },
  ])
  assert.deepEqual(candidateTargets("linux", {}), [])
})

test("candidateTargets is a single fixed utility on macOS and Windows", () => {
  assert.deepEqual(candidateTargets("darwin", {}), [{ method: "pbcopy", command: "pbcopy", args: [] }])
  assert.deepEqual(candidateTargets("win32", {}), [{ method: "clip.exe", command: "clip.exe", args: [] }])
})

test("oscCopySequence base64-encodes the payload inside the OSC 52 escape", () => {
  assert.equal(oscCopySequence("hi"), "\x1b]52;c;aGk=\x07")
})

test("copyToClipboard uses the first candidate that succeeds", () => {
  const calls: Array<[string, string[], string]> = []
  const result = copyToClipboard("hello world", {
    platform: "linux",
    env: { WAYLAND_DISPLAY: "wayland-0" },
    spawn: (command, args, input) => {
      calls.push([command, args, input])
      return true
    },
  })
  assert.deepEqual(calls, [["wl-copy", [], "hello world"]])
  assert.deepEqual(result, { method: "wl-copy", chars: "hello world".length })
})

test("copyToClipboard falls through a failing utility to the next candidate", () => {
  const attempted: string[] = []
  const result = copyToClipboard("text", {
    platform: "linux",
    env: { WAYLAND_DISPLAY: "wayland-0", DISPLAY: ":0" },
    spawn: (command) => {
      attempted.push(command)
      return command === "xclip"
    },
  })
  assert.deepEqual(attempted, ["wl-copy", "xclip"])
  assert.equal(result.method, "xclip")
})

test("copyToClipboard falls back to OSC 52 when no utility exists or succeeds, e.g. over SSH", () => {
  let written = ""
  const result = copyToClipboard("secret text", {
    platform: "linux",
    env: {},
    spawn: () => {
      throw new Error("ENOENT: command not found")
    },
    writeOsc52: (sequence) => {
      written = sequence
    },
  })
  assert.equal(result.method, "osc52")
  assert.equal(result.chars, "secret text".length)
  assert.equal(written, oscCopySequence("secret text"))
})

test("copyToClipboard falls back to OSC 52 when every utility exits non-zero", () => {
  const result = copyToClipboard("x", {
    platform: "darwin",
    env: {},
    spawn: () => false,
    writeOsc52: () => {},
  })
  assert.equal(result.method, "osc52")
})
