// The home screen's lists and the dots: which task is waiting for the person, which is news. No DOM.
import assert from "node:assert/strict";
import test from "node:test";

import { homeGroups, isUnseen, loadSeen, markSeen, railSignal, relativeTime, rowSignal } from "../js/attention.js";

const memory = () => {
  const data = new Map();
  return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
};

const task = (id, status, finished_at = null, extra = {}) => ({ id, status, finished_at, created_at: "2026-11-20T10:00:00", ...extra });

test("the first visit sets a baseline, so old finished tasks are not news", () => {
  const seen = loadSeen(memory(), "2026-11-23T12:00:00");
  assert.equal(isUnseen(task(1, "done", "2026-11-22T09:00:00"), seen), false);
  assert.equal(isUnseen(task(2, "done", "2026-11-23T13:00:00"), seen), true);
});

test("opening a task makes it seen until it finishes again", () => {
  const storage = memory();
  const seen = loadSeen(storage, "2026-11-23T12:00:00");
  const done = task(2, "done", "2026-11-23T13:00:00");
  assert.equal(markSeen(seen, done, storage), true);
  assert.equal(isUnseen(done, seen), false);
  assert.equal(isUnseen({ ...done, finished_at: "2026-11-23T15:00:00" }, seen), true);
  assert.equal(isUnseen(done, loadSeen(storage)), false, "remembered across a reload");
});

test("each task sits in one list: waiting, news, running, then the recent rest", () => {
  const seen = loadSeen(memory(), "2026-11-23T12:00:00");
  const tasks = [
    task(1, "waiting_for_user"),
    task(2, "done", "2026-11-23T13:00:00"),
    task(3, "running", null, { started_at: "2026-11-23T13:30:00" }),
    task(4, "done", "2026-11-21T09:00:00"),
    task(5, "paused", "2026-11-23T14:00:00"),
  ];
  const groups = homeGroups(tasks, seen);
  assert.deepEqual(groups.waiting.map((t) => t.id), [5, 1]);
  assert.deepEqual(groups.unseen.map((t) => t.id), [2]);
  assert.deepEqual(groups.running.map((t) => t.id), [3]);
  assert.deepEqual(groups.recent.map((t) => t.id), [4]);
});

test("yellow wins over blue on the rail, and a quiet account shows nothing", () => {
  const seen = loadSeen(memory(), "2026-11-23T12:00:00");
  assert.equal(railSignal([task(2, "done", "2026-11-23T13:00:00"), task(1, "waiting_for_user")], seen), "asks");
  assert.equal(railSignal([task(2, "done", "2026-11-23T13:00:00")], seen), "news");
  assert.equal(railSignal([task(4, "done", "2026-11-21T09:00:00")], seen), "");
  assert.equal(rowSignal(task(3, "running"), seen), "running");
});

test("times read the way people say them", () => {
  const now = new Date("2026-11-23T12:00:00").getTime();
  assert.equal(relativeTime("2026-11-23T11:55:00", "ru", now), "5 мин назад");
  assert.equal(relativeTime("2026-11-23T09:00:00", "en", now), "3 h ago");
  assert.equal(relativeTime("2026-11-22T09:00:00", "ru", now), "вчера");
});
