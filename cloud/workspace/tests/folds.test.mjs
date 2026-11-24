import assert from "node:assert/strict";
import { test } from "node:test";

import { FOLD_MS, Folds, HOLD_MS } from "../js/folds.js";

const call = (id, phase = "ok") => ({ id, kind: "call", phase });
const work = (id, ...calls) => ({ kind: "work", id, items: calls });
const reply = { kind: "reply", id: "r" };

test("a running group stays open; a finished one holds, then folds", () => {
  const folds = new Folds();
  folds.settle([work("w", call(1), call(2, "run"))], true, 0);
  assert.equal(folds.openness("w", 5000), 1, "still running");

  // Finished, but nothing follows yet and the turn goes on: the next call may join it.
  folds.settle([work("w", call(1), call(2))], true, 0);
  assert.equal(folds.openness("w", 5000), 1);

  folds.settle([work("w", call(1), call(2)), reply], true, 1000);
  assert.equal(folds.openness("w", 1000 + HOLD_MS), 1, "held for two seconds");
  assert.ok(folds.openness("w", 1000 + HOLD_MS + FOLD_MS / 2) < 1, "folding");
  assert.equal(folds.openness("w", 1000 + HOLD_MS + FOLD_MS), 0, "folded");
  assert.equal(folds.moving(1000 + HOLD_MS + FOLD_MS + 1), false);
});

test("the end of the turn settles the last group", () => {
  const folds = new Folds();
  folds.settle([work("w", call(1), call(2))], false, 0);
  assert.equal(folds.openness("w", HOLD_MS + FOLD_MS), 0);
});

test("a call joining a folded group opens it again from where it stood", () => {
  const folds = new Folds();
  folds.settle([work("w", call(1), call(2)), reply], true, 0);
  const now = HOLD_MS + FOLD_MS;
  folds.settle([work("w", call(1), call(2), call(3, "run"))], true, now);
  assert.equal(folds.openness("w", now), 0, "starts where it was, no jump");
  assert.equal(folds.openness("w", now + FOLD_MS), 1);
});

test("an old conversation opens folded, without the hold", () => {
  const folds = new Folds();
  folds.settle([work("w", call(1), call(2)), reply], false, 0, { quiet: true });
  assert.equal(folds.openness("w", 0), 0);
});

test("the person's own toggle wins and eases from where the group stood", () => {
  const folds = new Folds();
  folds.settle([work("w", call(1), call(2)), reply], false, 0, { quiet: true });
  folds.toggle("w", 10);
  assert.equal(folds.openness("w", 10), 0);
  assert.equal(folds.openness("w", 10 + FOLD_MS), 1);
  folds.settle([work("w", call(1), call(2)), reply], false, 10 + FOLD_MS);
  assert.equal(folds.openness("w", 10 + FOLD_MS * 3), 1, "the hold does not fold what the person opened");
});
