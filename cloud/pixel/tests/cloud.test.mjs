// The page's pure logic: how tasks become rail rows, what stands in place of the composer, what the
// account can do. No DOM, no network.
import assert from "node:assert/strict";
import test from "node:test";

import { canResume, cannotRun, duration, firstLine, folderOf, isPlumbing, listItem, nameProblem, parseLocation, projectName, projectUrl, repoLabel, sessionUrl, share, suggestedName, tasksOf, warnings, workspaceOf } from "../js/cloud.js";

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

test("tasks group by project and list as sessions named by their id", () => {
  const tasks = [
    { id: 3, public_id: "Xy12_abcDE", goal: "fix it\nmore words", project_id: 7, status: "running", created_at: "2026-11-06T10:00:00" },
    { id: 2, goal: "write notes", project_id: null, status: "done", finished_at: "2026-11-05T10:00:00" },
    { id: 1, goal: "older", project_id: 7, status: "failed" },
  ];
  assert.deepEqual(tasksOf(tasks, "7").map((task) => task.id), [3, 1]);
  assert.deepEqual(tasksOf(tasks, "").map((task) => task.id), [2]);
  assert.equal(workspaceOf(tasks[0]), "7");
  assert.equal(workspaceOf(tasks[1]), "");
  assert.deepEqual(listItem(tasks[0]), { name: "3", public_id: "Xy12_abcDE", title: "fix it", updated_at: "2026-11-06T10:00:00", closed: false });
  assert.equal(listItem(tasks[1]).closed, true);
  assert.equal(firstLine("  one\ntwo"), "one");
});

test("a project is shown by its name; the older tasks have a shared label", () => {
  const projects = [{ id: 7, name: "My app" }];
  assert.equal(projectName("7", projects, "none"), "My app");
  assert.equal(projectName("", projects, "none"), "none");
});

test("project names follow the server's rules, and repeats are caught however they are spelled", () => {
  const projects = [{ id: 1, name: "My app" }];
  for (const name of ["Новый", "site", "a.b_c-d", "x".repeat(60), "日本語"]) assert.equal(nameProblem(name, projects), "", name);
  for (const name of ["My app", "my app", "MY APP", "my-app", " My  app "]) assert.equal(nameProblem(name, projects), "name_taken", name);
  for (const name of ["", "   ", "../x", "a/b", "a\\b", ".hidden", "-x", "x.", "x".repeat(61), "task-7", "Task-12"]) assert.equal(nameProblem(name, projects), "bad_project_name", JSON.stringify(name));
  assert.equal(folderOf("  My   app "), "My-app");
});

test("a repository suggests its name for the project", () => {
  assert.equal(suggestedName("anthropics/claude-code"), "claude-code");
  assert.equal(suggestedName("repo.git"), "repo");
});

test("only a paused task is continued by a button; every other task is continued by writing to it", () => {
  assert.equal(canResume(null), false);
  assert.equal(canResume({ status: "paused" }), true);
  for (const status of ["queued", "starting", "running", "waiting_for_user", "done", "cancelled", "failed"]) assert.equal(canResume({ status }), false);
});

test("a message is held back, with the reason, until the account can run it", () => {
  const ready = { sub: { status: "trialing", balance_seconds: 60 }, key: { set: true } };
  assert.equal(cannotRun(ready), null);
  assert.equal(cannotRun({ ...ready, sub: { status: "none" } }), "no_subscription");
  assert.equal(cannotRun({ ...ready, key: { set: false, provider: "chatgpt" } }), "chatgpt_login");
  assert.equal(cannotRun({ ...ready, key: { set: false, provider: "" } }), "no_key");
  assert.equal(cannotRun({ ...ready, sub: null }), "no_subscription");
  assert.equal(cannotRun({ ...ready, sub: { status: "active", balance_seconds: 0 } }), "no_hours");
  assert.equal(cannotRun({ ...ready, key: { set: false } }), "no_key");
});

test("a session is addressed by its own id, and an address says exactly what it asks for", () => {
  assert.equal(sessionUrl("aB3_xY-9kQ"), "/pixel/s/aB3_xY-9kQ");
  assert.equal(projectUrl(7), "/pixel/p/7");
  assert.deepEqual(parseLocation("/pixel/s/aB3_xY-9kQ2"), { kind: "session", id: "aB3_xY-9kQ2" });
  assert.deepEqual(parseLocation("/pixel/s/aB3_xY-9kQ2/"), { kind: "session", id: "aB3_xY-9kQ2" });
  assert.deepEqual(parseLocation("/pixel/p/12"), { kind: "project", id: "12" });
  assert.deepEqual(parseLocation("/pixel/", "#/tasks/5"), { kind: "task", id: "5" });
  for (const bad of ["/pixel/", "/pixel/s/", "/pixel/s/abc", "/pixel/s/../x", "/pixel/s/a b c d e f", "/pixel/p/x", "/other/s/aB3_xY-9kQ2"]) assert.equal(parseLocation(bad), null, bad);
});

test("what Kvist says about opening or switching a session never reaches the feed, or it would wipe it", () => {
  assert.equal(isPlumbing({ type: "command_result", name: "new", ok: true, effects: { session: "task-7", changed: true } }, false), true);
  assert.equal(isPlumbing({ type: "command_result", name: "session", ok: true }, false), true);
  assert.equal(isPlumbing({ type: "command_result", name: "role", ok: true }, false), false);
  assert.equal(isPlumbing({ type: "hello" }, true), true);
  assert.equal(isPlumbing({ type: "hello" }, false), false);
  assert.equal(isPlumbing({ type: "final" }, true), false);
});

test("warnings come before the trouble: no plan, no hours, few hours, no key, no project, a stopped task", () => {
  const fine = { ready: true, sub: { status: "active", balance_seconds: 4 * 3600, plan_seconds: 5 * 3600 }, key: { set: true }, machine: { display_status: "ready" }, projects: [{ id: 1 }] };
  const ids = (state, agent) => warnings(state, agent).map((item) => item.id);
  assert.deepEqual(ids(fine), []);
  assert.deepEqual(ids({ ...fine, sub: null }), ["no-plan"]);
  assert.deepEqual(ids({ ...fine, sub: { ...fine.sub, balance_seconds: 0 } }), ["no-hours"]);
  assert.deepEqual(ids({ ...fine, sub: { ...fine.sub, balance_seconds: 20 * 60 } }), ["low-hours"]);
  assert.deepEqual(ids({ ...fine, sub: { ...fine.sub, balance_seconds: 0.05 * 5 * 3600 + 4000, plan_seconds: 100 * 3600 } }), ["low-hours"]);
  assert.deepEqual(ids({ ...fine, key: { set: false } }), ["no-key"]);
  assert.deepEqual(ids({ ...fine, projects: [] }), ["no-projects"]);
  assert.deepEqual(ids({ ...fine, ready: false, projects: [] }), []);
  assert.deepEqual(ids({ ...fine, machine: { display_status: "error", detail: "x" } }), ["machine-error"]);
  // A person cannot act on a code: a known one gets a sentence of its own and a retry, with the code kept in it.
  const trouble = (detail) => warnings({ ...fine, machine: { display_status: "error", detail } })[0];
  assert.deepEqual([trouble("wake_failed").text, trouble("wake_failed").value, trouble("wake_failed").action],
    ["warn machine wake_failed", "wake_failed", "machine"]);
  assert.equal(trouble("create_failed").action, undefined);
  assert.equal(trouble("something_new").text, "warn machine error");
  assert.deepEqual(ids(fine, { cloud: { status: "paused", reason: "hours_exhausted" } }), ["paused-hours_exhausted"]);
  assert.deepEqual(ids(fine, { cloud: { status: "paused", reason: "user" } }), []);
  assert.deepEqual(ids(fine, { cloud: { status: "failed", reason: "agent_error" } }), ["failed"]);
  assert.equal(warnings({ ...fine, sub: { ...fine.sub, balance_seconds: 0 } })[0].level, "error");
  assert.deepEqual(warnings(fine, null, [{ id: "session-missing", level: "error", text: "x" }]).map((item) => item.id), ["session-missing"]);
});
