import assert from "node:assert/strict"
import { test } from "node:test"

import { resolveEnter, routeOf, setPluginCommands, suggest } from "../commands.js"

// Plain plugins (docs/PLUGINS.md) come from the core's manifest and travel as ordinary requests ("goal").
test("plugins from the manifest are offered and routed as goals, never over a built-in", () => {
  setPluginCommands([{ name: "novibecode", description: "Teach instead of writing the code." }, { name: "new", description: "taken" }])
  try {
    assert.deepEqual(suggest("/novi").map((command) => [command.name, command.hint]), [["novibecode", "Teach instead of writing the code."]])
    assert.equal(routeOf("novibecode"), "goal")
    assert.equal(routeOf("new"), "core")
    assert.deepEqual(resolveEnter("/novi", 0), { kind: "submit", text: "/novibecode" })
  } finally {
    setPluginCommands([])
  }
  assert.equal(routeOf("novibecode"), null)
})
