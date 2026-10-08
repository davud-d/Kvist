import assert from "node:assert/strict"
import { test } from "node:test"

import { options } from "../build.mjs"

test("the bundle runs React's production build, which keeps no per-render performance entries", () => {
  assert.equal(options.define["process.env.NODE_ENV"], '"production"')
})
