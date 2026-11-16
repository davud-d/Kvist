import { afterEach, describe, expect, test } from "bun:test"

import { mount, type Mounted } from "./helpers.js"

let app: Mounted | undefined
afterEach(() => {
  app?.destroy()
  app = undefined
})

// `/council` and `/best-of` are turns that a command starts (UI_API.md §8c): the stand-in core answers like the real one,
// with the turn's events, `turn_end`, then `command_result`.
describe("commands that are turns", () => {
  for (const manifest of [true, false]) {
    test(`/council <question> shows its answer and leaves the prompt free (${manifest ? "listed by the core's manifest" : "typed by hand"})`, async () => {
      app = await mount({ height: 40, env: manifest ? { FAKE_MANIFEST: "1" } : {} })
      await app.waitFor("fake-fast")
      await app.type("/council is it simple")
      app.enter()
      await app.waitFor("The chosen decision: use security")
      // The prompt takes the next request: nothing is left running.
      await app.type("hello again")
      app.enter()
      await app.waitFor("done: hello again")
      expect(app.frame()).not.toContain("Wait for the current run")
    })
  }

  test("a council with no question is refused by the core and leaves nothing running", async () => {
    app = await mount({ height: 40, env: { FAKE_MANIFEST: "1" } })
    await app.waitFor("fake-fast")
    await app.type("/council")
    app.enter()
    await app.settle(300)
    await app.type("hello again")
    app.enter()
    await app.waitFor("done: hello again")
  })

  test("/best-of asks for its decision as an ordinary choice, and the verdict is on screen", async () => {
    app = await mount({ height: 40, env: { FAKE_MANIFEST: "1" } })
    await app.waitFor("fake-fast")
    await app.type("/best-of 2 fix the bug")
    app.enter()
    await app.waitFor("winner: e1 (tests passed)")
    await app.waitFor("Accept e1")
    app.enter()
    await app.waitFor("The result was accepted")
  })
})
