import assert from "node:assert/strict";
import { test } from "node:test";
import { commandLine, diffLines, hoursMinutes, percentLeft, runoutForecast, wholeMinutes } from "../src/js/format.js";

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

const DAY = 24 * 3600 * 1000;
const iso = (ms) => new Date(ms).toISOString();

// 1 h used over 10 of 30 days with 4 h left: the pace of the whole period carried forward.
const forecast = (extra = {}) => runoutForecast({
  planSeconds: 50 * 3600, balanceSeconds: 4 * 3600, usedSeconds: 3600,
  periodStart: iso(0), periodEnd: iso(30 * DAY), nowMs: 10 * DAY, ...extra,
});

test("a runout is not guessed without a pace", () => {
  assert.equal(forecast({ usedSeconds: 0 }), null);
  assert.equal(forecast({ planSeconds: 0 }), null);
  assert.equal(forecast({ nowMs: 30 * 60 * 1000 }), null);
});

test("an empty balance says so", () => {
  const empty = forecast({ balanceSeconds: 0 });
  assert.equal(empty.tone, "empty");
  assert.equal(empty.at, null);
  assert.equal(empty.total, 3600);
});

test("a pace that lasts past the next charge is plenty", () => {
  const result = forecast({ balanceSeconds: 10 * 3600 });
  assert.equal(result.tone, "plenty");
  assert.ok(result.at > 30 * DAY); // 10 h left, 1 h a day: another 100 days
  assert.equal(result.projected, 3 * 3600); // ten days of thirty would use 3 h of the 14 h left
});

test("a runout just before the charge is tight", () => {
  const result = runoutForecast({
    planSeconds: 50 * 3600, usedSeconds: 4 * 3600, balanceSeconds: 0.78 * 3600,
    periodStart: iso(0), periodEnd: iso(30 * DAY), nowMs: 25 * DAY,
  });
  assert.equal(result.tone, "tight"); // 4.875 days of pace from day 25: three hours before the charge
});

test("a runout well before the charge is early", () => {
  const result = runoutForecast({
    planSeconds: 50 * 3600, usedSeconds: 4 * 3600, balanceSeconds: 0.6 * 3600,
    periodStart: iso(0), periodEnd: iso(30 * DAY), nowMs: 25 * DAY,
  });
  assert.equal(result.tone, "early");
});

test("a period that will use under a third suggests a lower plan", () => {
  const base = {
    planSeconds: 50 * 3600, usedSeconds: 3600, balanceSeconds: 49 * 3600,
    periodStart: iso(0), periodEnd: iso(30 * DAY), nowMs: 25 * DAY,
  };
  const lower = runoutForecast({ ...base, lowerable: true });
  assert.equal(lower.tone, "lower");
  assert.equal(lower.at, 30 * DAY); // the next charge, not a date past it
  assert.equal(lower.projected, (3600 * 30) / 25);
  assert.equal(lower.total, 50 * 3600);
  assert.equal(runoutForecast(base).tone, "plenty"); // Start has no cheaper paid plan below it
});
