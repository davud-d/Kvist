import assert from "node:assert/strict";
import { test } from "node:test";
import { banner, cannotRun, hoursLow, listItem, machineState, paymentProblem, setupSteps } from "../../js/rules.js";

const ready = () => ({
  ready: true,
  sub: { status: "active", plan: "start", balance_seconds: 40 * 3600, plan_seconds: 50 * 3600 },
  key: { set: true },
  projects: [{ id: 1 }],
  machines: [{ display_status: "ready" }],
});

test("what blocks a message, in order", () => {
  assert.equal(cannotRun({ sub: null }), "no_subscription");
  assert.equal(cannotRun({ sub: { status: "none" } }), "no_subscription");
  assert.equal(cannotRun({ sub: { status: "active", balance_seconds: 0 }, key: { set: true } }), "no_hours");
  assert.equal(cannotRun({ sub: { status: "active", balance_seconds: 5 }, key: { set: false } }), "no_key");
  assert.equal(cannotRun({ sub: { status: "active", balance_seconds: 5 }, key: { set: false, provider: "chatgpt" } }), "chatgpt_login");
  assert.equal(cannotRun(ready()), null);
});

test("the machine in one word", () => {
  assert.equal(machineState([]), "none");
  assert.equal(machineState([{ display_status: "waiting_for_you" }]), "working");
  assert.equal(machineState([{ display_status: "sleeping" }]), "asleep");
  assert.equal(machineState([{ display_status: "ready", updating: true }]), "updating");
});

test("setup is plan, then key, then project", () => {
  assert.deepEqual(setupSteps({ sub: null, key: null, projects: [] }).map((s) => s.done), [false, false, false]);
  assert.deepEqual(setupSteps(ready()).map((s) => s.done), [true, true, true]);
});

test("hours are low under half an hour or a tenth of the plan, never when out or unmetered", () => {
  assert.equal(hoursLow({ status: "active", balance_seconds: 1500, plan_seconds: 180000 }), true);
  assert.equal(hoursLow({ status: "active", balance_seconds: 20000, plan_seconds: 180000 }), false);
  assert.equal(hoursLow({ status: "active", balance_seconds: 17000, plan_seconds: 180000 }), true);
  assert.equal(hoursLow({ status: "active", balance_seconds: 0, plan_seconds: 180000 }), false);
  assert.equal(hoursLow({ status: "active", balance_seconds: 5, plan_seconds: 0, unmetered: true }), false);
});

test("one banner, the most urgent", () => {
  assert.equal(banner({ account: ready() }), null);
  assert.equal(banner({ account: ready(), link: "down" }).id, "link");
  assert.equal(banner({ account: { ...ready(), sub: null } }).action, "plans");
  assert.equal(banner({ account: { ...ready(), key: { set: false } } }).action, "key");
  const paused = banner({ account: ready(), task: { status: "paused", reason: "hours_exhausted" } });
  assert.equal(paused.text, "pause hours_exhausted");
  assert.equal(banner({ account: ready(), task: { status: "paused", reason: "something new" } }).text, "pause user");
  assert.equal(banner({ account: { ...ready(), machines: [{ display_status: "waking" }] } }).id, "machine");
  assert.equal(banner({ account: { ...ready(), ready: false } }), null, "nothing is said before the account has loaded");
});

test("a session as the list shows it", () => {
  const item = listItem({ id: 4, public_id: "abc", project_id: 2, goal: "Fix the bug\nmore", status: "running", created_at: "2026-11-24T10:00:00Z" });
  assert.equal(item.title, "Fix the bug");
  assert.equal(item.projectId, "2");
  assert.equal(item.working, true);
  assert.equal(listItem({ id: 1, public_id: "x", goal: "g", title: "Mine", status: "done" }).title, "Mine");
});

test("payment problems have words of their own; a closed sheet is not a problem", () => {
  assert.equal(paymentProblem({ code: "card_declined" }), "pay declined");
  assert.equal(paymentProblem({ code: "wallet_cancelled" }), "");
  assert.equal(paymentProblem({ code: "who knows" }), "pay failed");
});
