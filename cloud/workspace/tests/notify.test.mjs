import assert from "node:assert/strict";
import { test } from "node:test";

import { newsOf } from "../js/notify.js";

test("only a task that left work for a state that needs the person is news", () => {
  const before = new Map([[1, "running"], [2, "running"], [3, "running"], [4, "running"], [5, "done"], [6, "queued"]]);
  const tasks = [
    { id: 1, status: "done" },
    { id: 2, status: "waiting_for_user" },
    { id: 3, status: "paused" },
    { id: 4, status: "running" },
    { id: 5, status: "done" },
    { id: 6, status: "failed" },
    { id: 7, status: "done" },
  ];
  assert.deepEqual(newsOf(before, tasks).map((task) => task.id), [1, 2, 6]);
});
