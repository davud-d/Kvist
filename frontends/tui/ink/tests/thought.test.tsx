import assert from "node:assert/strict"
import { afterEach, test } from "node:test"

import { render } from "ink-testing-library"
import { createElement, type ReactElement } from "react"

import type { Item } from "../state.js"
import { ViewContext } from "../ui/context.js"
import { ClaudeItemView } from "../ui/skins/claude-code/claude.js"
import { ItemView } from "../ui/skins/kvist/items.js"
import { applySkin } from "../ui/theme.js"

const view = { workspace: "/work/project", info: { provider: "anymodel", model: "sonnet", role: "chat", session: "s", kvistVersion: "1.2.3" }, effort: "high", pointer: null, expanded: false }
const thought: Item = { id: 1, kind: "thought", seconds: 3, text: "**Reading the failure**\nthe fixture is stale" }

afterEach(() => applySkin("ink"))

function frame(element: ReactElement): string {
  const app = render(createElement(ViewContext.Provider, { value: view }, element))
  const out = app.lastFrame() ?? ""
  app.unmount()
  return out
}

for (const [skin, View] of [["ink", ItemView], ["claude", ClaudeItemView]] as const) {
  test(`a thought's **bold** is rendered like an answer's, not as asterisks (${skin} skin)`, () => {
    applySkin(skin)
    const out = frame(createElement(View, { item: thought, showDebug: false }))

    assert.ok(!out.includes("**"), out)
    assert.ok(out.includes("Reading the failure"), out)
    assert.ok(out.includes("the fixture is stale"), out)
  })
}
