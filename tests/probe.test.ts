import assert from "node:assert/strict"
import { PassThrough } from "node:stream"
import { test } from "node:test"

import { parseProbe, probeTerminal } from "../src/probe.js"

test("a device-attributes reply that lists 4 means Sixel", () => {
  assert.deepEqual(parseProbe("\x1b[?61;4;6;22c"), { sixel: true, cell: null })
  assert.deepEqual(parseProbe("\x1b[?62;22c"), { sixel: false, cell: null })
  assert.deepEqual(parseProbe(""), { sixel: false, cell: null })
})

test("the size of a cell comes straight from the terminal when it reports it", () => {
  assert.deepEqual(parseProbe("\x1b[6;19;9t\x1b[?61;4c"), { sixel: true, cell: { width: 9, height: 19 } })
})

test("or from the window's pixels and cells when it reports those", () => {
  assert.deepEqual(parseProbe("\x1b[4;760;1100t\x1b[8;40;110t\x1b[?62c").cell, { width: 10, height: 19 })
  assert.equal(parseProbe("\x1b[4;760;1100t\x1b[?62c").cell, null)
})

test("probing asks all the questions with the attributes last, and stops at their reply", async () => {
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: () => stdin })
  const written: string[] = []
  const stdout = { isTTY: true, write: (text: string) => (written.push(text), true) }
  const result = probeTerminal(stdin as unknown as NodeJS.ReadStream, stdout as unknown as NodeJS.WriteStream, 2000)
  assert.equal(written.join(""), "\x1b[16t\x1b[14t\x1b[18t\x1b[c")
  stdin.write("\x1b[6;20;10t")
  stdin.write("\x1b[?62;4;22c")
  assert.deepEqual(await result, { sixel: true, cell: { width: 10, height: 20 } })
})

test("a terminal that says nothing is given up on, and one without a tty is not asked", async () => {
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: () => stdin })
  const stdout = { isTTY: true, write: () => true }
  assert.deepEqual(await probeTerminal(stdin as unknown as NodeJS.ReadStream, stdout as unknown as NodeJS.WriteStream, 30), { sixel: false, cell: null })
  const pipe = Object.assign(new PassThrough(), { isTTY: false })
  assert.deepEqual(await probeTerminal(pipe as unknown as NodeJS.ReadStream, stdout as unknown as NodeJS.WriteStream), { sixel: false, cell: null })
})
