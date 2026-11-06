// The page's pure logic: how tasks become rail rows, what stands in place of the composer, what the
// account can do. No DOM, no network.
import assert from "node:assert/strict";
import test from "node:test";

import { cannotRun, duration, firstLine, listItem, repoLabel, repositories, share, taskBanner, tasksOf } from "../js/cloud.js";

const say = (key) => key;

test("hours are whole minutes, rounded down, in the person's language", () => {
  assert.equal(duration(0, "en"), "0 min");
  assert.equal(duration(59, "en"), "0 min");
  assert.equal(duration(3600, "en"), "1 h");
  assert.equal(duration(5 * 3600 - 61, "en"), "4 h 58 min");
  assert.equal(duration(2 * 3600 + 60, "ru"), "2 ч 1 мин");
  assert.equal(duration(-500, "en"), "0 min");
});

test("the meter share stays between nothing and everything", () => {
  assert.equal(share(0, 100), 0);
  assert.equal(share(50, 100), 0.5);
  assert.equal(share(500, 100), 1);
  assert.equal(share(-5, 100), 0);
  assert.equal(share(5, 0), 0);
});

test("a repository is shown as owner/repo, the empty folder as nothing", () => {
  assert.equal(repoLabel("https://github.com/anthropics/claude-code.git"), "anthropics/claude-code");
  assert.equal(repoLabel("https://github.com/o/r/"), "o/r");
  assert.equal(repoLabel(""), "");
});

test("tasks group by repository and list as sessions named by their id", () => {
  const tasks = [
    { id: 3, goal: "fix it\nmore words", repo_url: "https://github.com/o/r", status: "running", created_at: "2026-11-06T10:00:00" },
    { id: 2, goal: "write notes", repo_url: "", status: "done", finished_at: "2026-11-05T10:00:00" },
    { id: 1, goal: "older", repo_url: "https://github.com/o/r", status: "failed" },
  ];
  assert.deepEqual(repositories(tasks), ["https://github.com/o/r", ""]);
  assert.deepEqual(tasksOf(tasks, "https://github.com/o/r").map((task) => task.id), [3, 1]);
  assert.deepEqual(listItem(tasks[0]), { name: "3", title: "fix it", updated_at: "2026-11-06T10:00:00", closed: false });
  assert.equal(listItem(tasks[1]).closed, true);
  assert.equal(firstLine("  one\ntwo"), "one");
});

test("once a task has its goal the composer gives way to one line, and Enter does the one thing that makes sense", () => {
  assert.equal(taskBanner(null, say), null);
  assert.deepEqual(taskBanner({ status: "queued" }, say), { text: "queued banner", action: null });
  for (const status of ["starting", "running"]) assert.deepEqual(taskBanner({ status }, say), { text: "working banner", action: null });
  assert.deepEqual(taskBanner({ status: "waiting_for_user" }, say), { text: "waiting banner", action: null });
  assert.deepEqual(taskBanner({ status: "paused", reason: "hours_exhausted" }, say), { text: "paused banner (pause: hours_exhausted)", action: "resume" });
  assert.deepEqual(taskBanner({ status: "paused", reason: "" }, say), { text: "paused banner", action: "resume" });
  for (const status of ["done", "cancelled", "failed"]) assert.deepEqual(taskBanner({ status }, say), { text: "finished banner", action: "new" });
});

test("a goal is held back, with the reason, until the account can run it", () => {
  const ready = { sub: { status: "trialing", balance_seconds: 60 }, key: { set: true } };
  assert.equal(cannotRun(ready), null);
  assert.equal(cannotRun({ ...ready, sub: { status: "none" } }), "no_subscription");
  assert.equal(cannotRun({ ...ready, sub: null }), "no_subscription");
  assert.equal(cannotRun({ ...ready, sub: { status: "active", balance_seconds: 0 } }), "no_hours");
  assert.equal(cannotRun({ ...ready, key: { set: false } }), "no_key");
});
