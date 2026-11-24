import assert from "node:assert/strict";
import { test } from "node:test";
import { clock, dayBucket, duration, localized, money, repoLabel, share, wholeMinutes } from "../../js/format.js";

test("durations round minutes down, as they are billed", () => {
  assert.equal(wholeMinutes(119), 1);
  assert.equal(duration(4 * 3600 + 48 * 60 + 59, "en"), "4 h 48 min");
  assert.equal(duration(2 * 3600, "ru"), "2 ч");
  assert.equal(duration(59, "en"), "0 min");
  assert.equal(duration(-5, "en"), "0 min");
});

test("a running clock", () => {
  assert.equal(clock(5), "0:05");
  assert.equal(clock(125), "2:05");
  assert.equal(clock(3725), "1:02:05");
});

test("the share of hours left stays between zero and one", () => {
  assert.equal(share(25, 50), 0.5);
  assert.equal(share(-5, 50), 0);
  assert.equal(share(80, 50), 1);
  assert.equal(share(5, 0), 0);
});

test("day buckets", () => {
  const now = new Date(2026, 10, 24, 15, 0);
  assert.equal(dayBucket(new Date(2026, 10, 24, 1, 0).toISOString(), now), "today");
  assert.equal(dayBucket(new Date(2026, 10, 23, 23, 0).toISOString(), now), "yesterday");
  assert.equal(dayBucket(new Date(2026, 10, 20, 9, 0).toISOString(), now), "week");
  assert.equal(dayBucket(new Date(2026, 9, 1).toISOString(), now), "older");
  assert.equal(dayBucket("not a date", now), "older");
});

test("money as the server states it", () => {
  assert.equal(money("39.00", "USD"), "$39");
  assert.equal(money("12.50", "EUR"), "€12.50");
  assert.equal(money("5", "GBP"), "£5");
  assert.equal(money("x", "USD"), "");
  assert.equal(money("100", "RUB"), "RUB 100");
});

test("the server's bilingual messages are split by language", () => {
  assert.equal(localized("Start the trial first. / Сначала начни пробный период.", "en"), "Start the trial first.");
  assert.equal(localized("Start the trial first. / Сначала начни пробный период.", "ru"), "Сначала начни пробный период.");
  assert.equal(localized("plain", "ru"), "plain");
});

test("a repository link as owner/repo", () => {
  assert.equal(repoLabel("https://github.com/octo/cat.git"), "octo/cat");
  assert.equal(repoLabel(""), "");
});
