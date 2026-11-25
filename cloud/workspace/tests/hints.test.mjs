import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// The phone page and this one speak the same lines from the same file; the phone's tests cover what it does.
test("the field's hints are the phone page's file, unchanged", () => {
  const here = readFileSync(new URL("../js/hints.js", import.meta.url), "utf8");
  const phone = readFileSync(new URL("../../mobile/js/hints.js", import.meta.url), "utf8");
  assert.equal(here, phone, "copy cloud/mobile/js/hints.js to cloud/workspace/js/hints.js");
});
