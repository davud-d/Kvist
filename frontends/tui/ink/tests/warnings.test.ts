import assert from "node:assert/strict"
import { mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { test } from "node:test"

import { routeWarningsToLog } from "../warnings.js"

test("a process warning lands in the log file and not on stderr", async () => {
  const file = path.join(mkdtempSync(path.join(tmpdir(), "kvist-warn-")), "logs", "tui.log")
  const written: string[] = []
  const write = process.stderr.write.bind(process.stderr)
  process.stderr.write = ((chunk: string | Uint8Array) => (written.push(String(chunk)), true)) as typeof process.stderr.write
  try {
    routeWarningsToLog(file)
    process.emitWarning("too many listeners", "MaxListenersExceededWarning")
    await new Promise((resolve) => setImmediate(resolve))
  } finally {
    process.stderr.write = write
  }
  assert.match(readFileSync(file, "utf8"), /MaxListenersExceededWarning: too many listeners/)
  assert.deepEqual(written, [])
})
