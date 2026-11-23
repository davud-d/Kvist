import assert from "node:assert/strict";
import { test } from "node:test";

import { orbState } from "../js/views/orb.js";

const agent = (items, state = "running") => ({ state, items });

test("the orb shows what the agent is about", () => {
  assert.equal(orbState(agent([], "idle")), null, "nothing when the agent is not at work");
  assert.equal(orbState(agent([])), "think", "the first moments of a turn");
  assert.equal(orbState(agent([{ kind: "reply", live: true }])), "speak");
  // A call is shown from the moment it is planned, before its arguments arrive.
  assert.equal(orbState(agent([{ kind: "call", phase: "run", actionType: "run_shell" }])), "shell");
  assert.equal(orbState(agent([{ kind: "call", phase: "run", actionType: "read_file" }])), "read");
  assert.equal(orbState(agent([{ kind: "call", phase: "run", actionType: "append_file" }])), "write");
  assert.equal(orbState(agent([{ kind: "call", phase: "run", actionType: "replace_in_file" }])), "edit");
  assert.equal(orbState(agent([{ kind: "call", phase: "run", actionType: "skills" }])), "skill");
  assert.equal(orbState(agent([{ kind: "call", phase: "ok", actionType: "read_file" }])), "think", "a finished call: the model thinks again");
});
