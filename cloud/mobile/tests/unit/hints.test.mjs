import assert from "node:assert/strict";
import { test } from "node:test";

import { CONDITIONS, GENERAL, createPicker, eraseBursts, typingDelays } from "../../js/hints.js";

// A fixed sequence instead of Math.random, so a test sees the same order every run.
function seeded(seed = 7) {
  let state = seed;
  return () => {
    state = (state * 1_103_515_245 + 12_345) % 2_147_483_648;
    return state / 2_147_483_648;
  };
}

const quiet = { firstVisit: false, awayDays: 0, tasks: [], sub: null, createdAt: "", phone: false, typed: false, erasedAt: null, pageMs: 0 };
// A Wednesday at 15:00, the 10th of June: no hour, day or date has lines for it.
const plainDay = new Date(2026, 5, 10, 15, 0);
const live = (facts) => CONDITIONS.filter((condition) => condition.when({ ...quiet, ...facts })).map((condition) => condition.id);

test("every line has both languages and every condition three of its own", () => {
  for (const item of [...GENERAL, ...CONDITIONS.flatMap((condition) => condition.lines)]) {
    assert.ok(item.en.trim() && item.ru.trim(), JSON.stringify(item));
  }
  for (const condition of CONDITIONS) assert.equal(condition.lines.length, 3, condition.id);
  assert.equal(CONDITIONS.length, 27);
  const all = [...GENERAL, ...CONDITIONS.flatMap((condition) => condition.lines)].map((item) => item.ru);
  assert.equal(new Set(all).size, all.length, "no line is said by two conditions");
});

test("an ordinary afternoon has no condition, and the hours and days are told apart", () => {
  assert.deepEqual(live({ now: plainDay }), []);
  assert.deepEqual(live({ now: new Date(2026, 10, 30, 9) }), ["morning", "monday"]);
  assert.deepEqual(live({ now: new Date(2026, 10, 27, 13) }), ["lunch", "friday"]);
  assert.deepEqual(live({ now: new Date(2026, 10, 28, 2) }), ["night", "weekend"]);
  assert.deepEqual(live({ now: new Date(2026, 10, 26, 20) }), ["evening"]);
  // Friday the 13th has its own lines, not the ordinary Friday's.
  assert.deepEqual(live({ now: new Date(2026, 1, 13, 15) }), ["friday 13"]);
});

test("the Monday line is said only on a Monday", () => {
  const monday = CONDITIONS.find((condition) => condition.id === "monday");
  for (let day = 23; day <= 29; day += 1) {
    const date = new Date(2026, 10, day, 15);
    assert.equal(monday.when({ ...quiet, now: date }), date.getDay() === 1, date.toDateString());
  }
});

test("dates of the year", () => {
  assert.ok(live({ now: new Date(2026, 11, 31, 15) }).includes("new year's eve"));
  assert.ok(!live({ now: new Date(2026, 11, 31, 15) }).includes("holidays"));
  assert.ok(live({ now: new Date(2027, 0, 5, 15) }).includes("holidays"));
  assert.ok(live({ now: new Date(2026, 3, 1, 15) }).includes("april 1"));
  // Day 256: the 13th of September, the 12th in a leap year.
  assert.ok(live({ now: new Date(2026, 8, 13, 15) }).includes("programmers' day"));
  assert.ok(live({ now: new Date(2028, 8, 12, 15) }).includes("programmers' day"));
  assert.ok(live({ now: new Date(2026, 8, 1, 15) }).includes("september 1"));
  assert.ok(!live({ now: new Date(2026, 8, 1, 15) }).includes("first of the month"));
  assert.ok(live({ now: new Date(2026, 6, 1, 15) }).includes("first of the month"));
});

test("what the person did: tasks, the account, the page", () => {
  const now = plainDay;
  const at = (hours) => new Date(now - hours * 3_600_000).toISOString();
  assert.deepEqual(live({ now, tasks: [{ status: "paused", created_at: at(30) }] }), ["unfinished"]);
  assert.deepEqual(live({ now, tasks: [{ status: "done", finished_at: at(1), created_at: at(30) }, { status: "failed", finished_at: at(5), created_at: at(30) }] }), ["last went well"]);
  assert.deepEqual(live({ now, tasks: [{ status: "failed", finished_at: at(1), created_at: at(30) }, { status: "cancelled", finished_at: at(0.5), created_at: at(30) }] }), ["last failed"]);
  const today = [0.5, 1, 2].map((hours) => ({ status: "running", created_at: at(hours) }));
  assert.deepEqual(live({ now, tasks: today }), ["third today"]);
  const week = Array.from({ length: 10 }, (_, i) => ({ status: "running", created_at: at(30 + i * 10) }));
  assert.deepEqual(live({ now, tasks: week }), ["busy week"]);
  assert.deepEqual(live({ now, createdAt: new Date(2025, 5, 10, 8).toISOString() }), ["a year together"]);
  assert.deepEqual(live({ now, createdAt: new Date(2026, 5, 10, 8).toISOString() }), [], "the day of sign-up is not an anniversary");
  assert.deepEqual(live({ now, sub: { status: "trialing", trial_ends_at: at(-5) } }), ["trial ends"]);
  assert.deepEqual(live({ now, sub: { status: "trialing", trial_ends_at: at(-50) } }), []);
  assert.deepEqual(live({ now, firstVisit: true }), ["first visit"]);
  assert.deepEqual(live({ now, awayDays: 8 }), ["back after a week"]);
  assert.deepEqual(live({ now, phone: true }), ["phone"]);
  assert.deepEqual(live({ now, typed: true, erasedAt: new Date(now - 60_000) }), ["typed and erased"]);
  assert.deepEqual(live({ now, pageMs: 3 * 60_000 }), ["thinking"]);
  assert.deepEqual(live({ now, typed: true, pageMs: 3 * 3_600_000 }), ["long stay"]);
});

test("a condition's lines come every other time, each once, and the first line stays long", () => {
  const picker = createPicker(seeded());
  const facts = { ...quiet, now: new Date(2026, 10, 30, 15) };
  const monday = new Set(CONDITIONS.find((condition) => condition.id === "monday").lines);
  const picks = Array.from({ length: 12 }, () => picker.next(facts));
  assert.ok(picks[0].hold >= 120_000 && picks[0].hold <= 180_000);
  const said = picks.map((pick) => monday.has(pick.line));
  assert.deepEqual(said.slice(0, 6), [true, false, true, false, true, false]);
  assert.ok(said.slice(6).every((was) => !was), "three Monday lines, then only general ones");
  assert.equal(new Set(picks.filter((pick) => monday.has(pick.line)).map((pick) => pick.line)).size, 3);
});

test("most changes are rare: short lines come at most two in a row between long ones", () => {
  const picker = createPicker(seeded(3));
  const holds = Array.from({ length: 300 }, () => picker.next({ ...quiet, now: plainDay }).hold);
  for (const hold of holds) assert.ok((hold >= 15_000 && hold <= 25_000) || (hold >= 120_000 && hold <= 180_000), String(hold));
  const longs = holds.filter((hold) => hold >= 120_000).length;
  assert.ok(longs > 100 && longs < 200, `long ones: ${longs}`);
  for (let i = 2; i < holds.length; i += 1) assert.ok(!(holds[i] < 120_000 && holds[i - 1] < 120_000 && holds[i - 2] < 120_000), `three short at ${i}`);
});

test("general lines go round with no repeat until all were said", () => {
  const picker = createPicker(seeded(11));
  const round = Array.from({ length: GENERAL.length }, () => picker.next({ ...quiet, now: plainDay }).line);
  assert.equal(new Set(round).size, GENERAL.length);
});

test("a line that does not fit the field is skipped", () => {
  const picker = createPicker(seeded(5));
  const short = (line) => line.ru.length <= 26;
  const picks = Array.from({ length: 30 }, () => picker.next({ ...quiet, now: new Date(2026, 10, 30, 15) }, short).line);
  assert.ok(picks.every(short));
  // The Monday condition still speaks with the one line of it that fits.
  assert.ok(picks.some((line) => line.ru === "Новая неделя, новые задачи"));
});

test("typing slows after punctuation; erasing goes in bursts of two to four letters", () => {
  const delays = typingDelays("Да, ок. Ну", () => 0.5);
  assert.deepEqual(delays, [50, 50, 50, 200, 50, 50, 50, 350, 50, 50]);
  const bursts = eraseBursts(23, seeded());
  assert.equal(bursts.reduce((sum, burst) => sum + burst.size, 0), 23);
  for (const burst of bursts.slice(0, -1)) assert.ok(burst.size >= 2 && burst.size <= 4);
  for (const burst of bursts) assert.ok(burst.gap >= 60 && burst.gap <= 140);
});
