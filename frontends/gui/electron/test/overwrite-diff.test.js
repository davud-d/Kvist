import test from "node:test"
import assert from "node:assert/strict"

import { pairRows, parseUnifiedDiff } from "../renderer/overwrite-diff.js"

test("an overwrite diff is paired with removed lines left and added lines right", () => {
  const rows = parseUnifiedDiff("--- a/x\n+++ b/x\n@@ -2,3 +2,3 @@\n import os\n-DEBUG = False\n+DEBUG = True\n PORT = 1")
  assert.deepEqual(rows.map((r) => [r.kind, r.oldNo, r.newNo]), [["ctx", 2, 2], ["del", 3, null], ["add", null, 3], ["ctx", 4, 4]])
  const [, changed] = pairRows(rows)
  assert.equal(changed.left.text, "DEBUG = False")
  assert.equal(changed.right.text, "DEBUG = True")
})
