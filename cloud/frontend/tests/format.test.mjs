import assert from "node:assert/strict";
import { test } from "node:test";
import { commandLine, diffLines, hoursMinutes, percentLeft, wholeMinutes } from "../src/js/format.js";

const units = { "units.min": "{m} min", "units.h": "{h} h", "units.hm": "{h} h {m} min" };
const tr = (key, vars) => units[key].replace(/\{(\w+)\}/g, (_, name) => vars[name]);

test("minutes are whole and rounded down", () => {
  assert.equal(wholeMinutes(59), 0);
  assert.equal(wholeMinutes(119), 1);
  assert.equal(wholeMinutes(-5), 0);
  assert.equal(wholeMinutes("bad"), 0);
});

test("hours and minutes", () => {
  assert.equal(hoursMinutes(48 * 60 + 59, tr), "48 min");
  assert.equal(hoursMinutes(5 * 3600, tr), "5 h");
  assert.equal(hoursMinutes(4 * 3600 + 48 * 60, tr), "4 h 48 min");
});

test("percent left is clamped", () => {
  assert.equal(percentLeft(9000, 18000), 50);
  assert.equal(percentLeft(-10, 18000), 0);
  assert.equal(percentLeft(10, 0), 0);
});

test("command line from argv", () => {
  assert.equal(commandLine({ command: ["pytest", "-k", "a b"] }), 'pytest -k "a b"');
  assert.equal(commandLine({}), "");
});

test("diff lines are typed", () => {
  const lines = diffLines("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same\n");
  assert.deepEqual(lines.map((line) => line.kind), ["file", "file", "hunk", "del", "add", "ctx"]);
});
