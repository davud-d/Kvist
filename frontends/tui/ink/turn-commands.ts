import type { SlashCommand } from "./commands.js"

// The commands that are turns of the agent (UI_API.md §8c): `route: "turn"`, sent as a `command` message,
// answered with the turn's events, `turn_end`, then `command_result`.
//
// Kept apart from `COMMANDS` on purpose: the reference of Ink's commands that the core gives the model
// (`slash_commands_context()` in kernel/protocol/slash_commands.py) is checked against `COMMANDS` name for name
// (kernel/tests/test_slash_commands.py), and the core does not list these two for Ink yet. Move them into
// `COMMANDS` together with their two rows there.
export const TURN_COMMANDS: readonly SlashCommand[] = [
  { name: "council", route: "turn", hint: "Ask four read-only members an architecture question: /council <question>", needsArgument: true },
  { name: "best-of", route: "turn", hint: "Run 2 to 6 executors on one task and pick the best result: /best-of [N] <task>", needsArgument: true },
]
