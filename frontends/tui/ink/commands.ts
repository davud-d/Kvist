import type { Skin } from "./ui/theme.js"

// Where a command runs: here in the UI, in the core process over the
// `command` message (UI_API.md §8c), or only in the classic terminal.
export type Route = "local" | "core" | "classic"

export interface SlashCommand {
  name: string
  hint: string
  route: Route
  // Running it bare would only print a usage line, so Enter completes it instead.
  needsArgument?: boolean
}

export const COMMANDS: readonly SlashCommand[] = [
  { name: "help", route: "local", hint: "Show commands and keys" },
  { name: "new", route: "core", hint: "Start a new session (the current one is kept)" },
  { name: "session", route: "core", hint: "Pick a session, or /session <name> to switch" },
  { name: "rename", route: "core", hint: "Set the active session's title by hand", needsArgument: true },
  { name: "role", route: "core", hint: "Agent role: auto, coder, reader, devops, chat, universal" },
  { name: "effort", route: "core", hint: "Reasoning effort: false … max" },
  { name: "models", route: "core", hint: "Pick the fast and smart models" },
  { name: "web-search-provider", route: "core", hint: "Web search provider and its API key" },
  { name: "style", route: "core", hint: "Working style of the agent and the chat" },
  { name: "verbose", route: "core", hint: "Toggle verbose mode (on | off)" },
  { name: "changes", route: "core", hint: "Files the agent changed, across sessions" },
  { name: "context", route: "core", hint: "Context size of the model and how much is used" },
  { name: "revert", route: "core", hint: "Undo the agent's changes" },
  { name: "resume", route: "core", hint: "Continue an interrupted run" },
  { name: "provider", route: "core", hint: "Switch or configure the provider" },
  { name: "change-api", route: "core", hint: "Change the API key of the provider" },
  { name: "settings-api", route: "core", hint: "Request pacing, timeouts, context window" },
  { name: "strime", route: "core", hint: "Streaming for a custom provider (on | off)" },
  { name: "del-provider", route: "core", hint: "Delete the active custom provider" },
  { name: "full-reset", route: "core", hint: "Erase every saved provider and key" },
  { name: "copy", route: "local", hint: "Copy the last answer to the clipboard (/copy N for older)" },
  { name: "tui", route: "local", hint: "Choose the terminal interface" },
  { name: "debug", route: "local", hint: "Show or hide debug events" },
  { name: "clear", route: "local", hint: "Clear the screen" },
  { name: "quit", route: "local", hint: "Exit (also /exit, /bye)" },
]

export const NAME_WIDTH = Math.max(...COMMANDS.map((command) => command.name.length))

export const EFFORTS = ["false", "low", "medium", "high", "xhigh", "max"] as const

// The terminal interfaces a user can switch between; add one here and to the
// launcher (kernel/main.py) when a new one exists. `value` is what the launcher saves.
export const FRONTENDS = [
  { value: "tui", label: "Ink terminal", description: "Panels and a boxed input" },
  { value: "claude", label: "Claude-style terminal", description: "The Claude Code look: ● calls, ⎿ results, ruled input" },
  { value: "glass", label: "Kvist Glass", description: "Over a photograph, glass cards and a live browser (kitty, Ghostty)" },
  { value: "terminal", label: "Classic Legacy", description: "The original prompt_toolkit interface" },
] as const

// The saved `frontend` value of the interface a skin belongs to.
export function frontendOf(skin: Skin): string {
  return skin === "claude" ? "claude" : "tui"
}

export const FRONTEND_REQUEST_ID = "local:frontend"

export const CLASSIC_COMMAND = "python -m kernel --frontend terminal"

export function routeOf(name: string): Route | null {
  return COMMANDS.find((command) => command.name === name)?.route ?? null
}

// The core takes `switch|new|delete [NAME]`; a bare name means switch.
export function sessionArgs(args: string[]): string[] {
  const [head = ""] = args
  if (["switch", "new", "delete"].includes(head.toLowerCase())) return args
  return args.length ? ["switch", ...args.slice(0, 1)] : []
}

export function suggest(text: string, commands: readonly SlashCommand[] = COMMANDS): SlashCommand[] {
  if (!text.startsWith("/") || text.includes(" ")) return []
  const prefix = text.slice(1).toLowerCase()
  return commands.filter((command) => command.name.startsWith(prefix))
}

export interface Parsed {
  name: string
  args: string[]
}

export function parseSlash(text: string): Parsed | null {
  if (!text.startsWith("/")) return null
  const [head = "", ...args] = text.slice(1).trim().split(/\s+/)
  const name = head.toLowerCase()
  const alias = name === "exit" || name === "bye" ? "quit" : name
  return { name: alias, args }
}

// First row of a `size`-row window over `count` items that keeps `selected` visible.
export function windowStart(count: number, selected: number, size: number): number {
  if (count <= size) return 0
  return Math.min(Math.max(0, selected - size + 1), count - size)
}

// The bordered input box (2 borders around its content), its caption line, the
// footer below it and the counter line the suggestion list adds once it is
// capped are always on screen alongside the suggestions themselves. Ink
// redraws this whole live region in place by moving the cursor up by the
// *previous* frame's height; once that height reaches the terminal's row
// count, some of it has already scrolled off, so the next redraw can't erase
// it and Ink falls back to a full-screen clear (which also wipes scrollback)
// that keeps repeating on later frames even as the content shrinks back down.
// Both things that grow the frame are capped against the row count so it stays
// safely under it rather than flush with it: how many suggestions are listed,
// and how many rows of a pasted line the box shows.
export const MAX_VISIBLE_SUGGESTIONS = 6
export const MAX_BOX_ROWS = 4
const CHROME_ROWS = 5
const SAFETY_MARGIN = 2

export function visibleSuggestionCount(rows: number): number {
  return Math.max(1, Math.min(MAX_VISIBLE_SUGGESTIONS, rows - CHROME_ROWS - SAFETY_MARGIN - 1))
}

export function visibleBoxRows(rows: number): number {
  return Math.max(1, Math.min(MAX_BOX_ROWS, rows - CHROME_ROWS - SAFETY_MARGIN - visibleSuggestionCount(rows)))
}

export type EnterAction = { kind: "submit"; text: string } | { kind: "fill"; text: string }

// What Enter does for the text in the box while the suggestion list is open.
export function resolveEnter(text: string, selected: number, commands: readonly SlashCommand[] = COMMANDS): EnterAction {
  const command = suggest(text, commands)[selected]
  if (!command) return { kind: "submit", text }
  if (command.needsArgument) return { kind: "fill", text: `/${command.name} ` }
  return { kind: "submit", text: `/${command.name}` }
}
