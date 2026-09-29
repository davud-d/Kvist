import assert from "node:assert/strict"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { Text } from "ink"
import { createElement } from "react"

import { useWindowSize } from "../ui/window-size.js"

function Size() {
  const { columns } = useWindowSize()
  return createElement(Text, null, String(columns))
}

test("thirty components share one resize listener, so Node's limit of ten is never reached", () => {
  const app = render(createElement("ink-box" as never, null, ...Array.from({ length: 30 }, (_, index) => createElement(Size, { key: index }))))
  assert.equal(app.stdout.listenerCount("resize"), 1)
  app.unmount()
  assert.equal(app.stdout.listenerCount("resize"), 0)
})
