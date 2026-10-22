// How the core's actions are worded and which kind of card they get. Pure.
// OpenCode draws short calls as one muted line (inline) and calls that carry content
// (a command's output, a file, a diff) as a raised block; Kvist's actions fall into
// the same two groups.

import { diffLines, type DiffRow } from "../../ink/diff.js"
import { actionTarget } from "../../ink/state.js"

export interface Inline {
  icon: string
  text: string
  // Shown with a spinner while the call has no result yet.
  pending: string
}

export const str = (value: unknown): string => (typeof value === "string" ? value : "")

export function commandLine(args: Record<string, unknown>): string {
  const command = args.command
  return Array.isArray(command) ? command.map(String).join(" ") : str(command)
}

function lineRange(args: Record<string, unknown>): string {
  const start = typeof args.start_line === "number" ? args.start_line : null
  const end = typeof args.end_line === "number" ? args.end_line : null
  if (start === null && end === null) return ""
  return `:${start ?? 1}-${end ?? ""}`
}

function paths(args: Record<string, unknown>): string {
  return Array.isArray(args.paths) ? args.paths.map(String).join(", ") : ""
}

const quote = (text: string) => `"${text}"`

// One entry per action type; an action not listed falls back to its name and target.
const INLINE: Record<string, (args: Record<string, unknown>) => Inline> = {
  read_file: (a) => ({ icon: "→", text: `Read ${str(a.path)}${lineRange(a)}`, pending: "Reading file…" }),
  read_multiple_files: (a) => ({ icon: "→", text: `Read ${paths(a)}`, pending: "Reading files…" }),
  list_dir: (a) => ({ icon: "→", text: `List ${str(a.path) || "."}`, pending: "Listing directory…" }),
  list_tree: (a) => ({ icon: "→", text: `Tree ${str(a.path) || "."}`, pending: "Listing tree…" }),
  find_files: (a) => ({ icon: "✱", text: `Glob ${quote(str(a.pattern))}${a.path ? ` in ${str(a.path)}` : ""}`, pending: "Finding files…" }),
  search_code: (a) => ({ icon: "✱", text: `Grep ${quote(str(a.query))}${a.path ? ` in ${str(a.path)}` : ""}`, pending: "Searching content…" }),
  write_file: (a) => ({ icon: "←", text: `Write ${str(a.path)}`, pending: "Preparing write…" }),
  append_file: (a) => ({ icon: "←", text: `Append ${str(a.path)}`, pending: "Preparing append…" }),
  replace_in_file: (a) => ({ icon: "←", text: `Edit ${str(a.path)}`, pending: "Preparing edit…" }),
  git_status: () => ({ icon: "→", text: "Git status", pending: "Checking git status…" }),
  git_diff: (a) => ({ icon: "→", text: `Git diff${actionTarget(a) ? ` ${actionTarget(a)}` : ""}`, pending: "Reading git diff…" }),
  web_search: (a) => ({ icon: "◈", text: `Web Search ${quote(str(a.query))}`, pending: "Searching the web…" }),
  fetch_url: (a) => ({ icon: "%", text: `WebFetch ${str(a.url)}`, pending: "Fetching from the web…" }),
  delegate_task: (a) => ({ icon: "│", text: `Handoff ${str(a.target_role)}: ${str(a.task)}`, pending: "Recording handoff…" }),
  job_output: (a) => ({ icon: "→", text: `Job output ${str(a.job_id)}`, pending: "Reading job output…" }),
  job_stop: (a) => ({ icon: "■", text: `Stop job ${str(a.job_id)}`, pending: "Stopping job…" }),
  helper: (a) => ({ icon: "│", text: `Helper ${str(a.op)}${a.task ? `: ${str(a.task)}` : a.agent_id ? ` ${str(a.agent_id)}` : ""}`, pending: "Talking to helper…" }),
  skills: (a) => ({ icon: "⚙", text: `Skills ${str(a.op)}${a.skill ? ` ${str(a.skill)}` : ""}`, pending: "Working with skills…" }),
  memory: (a) => ({ icon: "⚙", text: `Memory ${str(a.op)}${a.query ? ` ${quote(str(a.query))}` : ""}`, pending: "Working with memory…" }),
  sessions: (a) => ({ icon: "⚙", text: `Sessions ${str(a.op)}${a.session ? ` ${str(a.session)}` : ""}`, pending: "Reading sessions…" }),
  ask_user: (a) => ({ icon: "→", text: `Ask ${quote(str(a.question))}`, pending: "Asking…" }),
  wait: (a) => ({ icon: "◷", text: `Wait ${typeof a.seconds === "number" ? `${a.seconds}s` : ""}${a.reason ? `: ${str(a.reason)}` : ""}`.trimEnd(), pending: "Setting a wait…" }),
  schedule: (a) => ({ icon: "◷", text: `Schedule ${str(a.op)}${a.task ? `: ${str(a.task)}` : ""}`, pending: "Updating schedule…" }),
  offer_plan: () => ({ icon: "→", text: "Offer a plan", pending: "Offering a plan…" }),
  propose_plan: () => ({ icon: "→", text: "Propose a plan", pending: "Writing a plan…" }),
  edit_plan: () => ({ icon: "→", text: "Revise the plan", pending: "Revising the plan…" }),
  cancel_plan: () => ({ icon: "■", text: "Cancel the plan", pending: "Cancelling the plan…" }),
  end_session: () => ({ icon: "■", text: "End session", pending: "Ending the session…" }),
}

export function inlineOf(action: string, args: Record<string, unknown>): Inline {
  const make = INLINE[action]
  if (make) return make(args)
  const target = actionTarget(args)
  return { icon: "⚙", text: `${action}${target ? ` ${target}` : ""}`, pending: `Running ${action}…` }
}

// The call that carries the final words: the answer follows it, so a line for the call would repeat it.
const ANSWER_ACTIONS = new Set(["final_answer", "report_to_user", "refuse"])

export function isAnswerAction(action: string): boolean {
  return ANSWER_ACTIONS.has(action)
}

export type BlockKind = "shell" | "write" | "append" | "edit"

// Calls that carry content get a block once they have a result; the rest stay inline.
export function blockOf(action: string): BlockKind | null {
  switch (action) {
    case "run_shell":
      return "shell"
    case "write_file":
      return "write"
    case "append_file":
      return "append"
    case "replace_in_file":
      return "edit"
    default:
      return null
  }
}

// A label such as "read_file  src/a.ts" (what the reducer keeps per explored step) turned
// back into the inline line it came from.
export function inlineFromLabel(label: string): Inline {
  const [action = "", ...rest] = label.split("  ")
  const target = rest.join("  ").trim()
  const args: Record<string, unknown> =
    action === "read_multiple_files" ? { paths: target.split(", ") }
      : action === "find_files" ? { pattern: target }
      : action === "search_code" ? { query: target }
      : action === "git_status" ? {}
      : { path: target }
  return inlineOf(action, args)
}

export const HUNK_CONTEXT = 3

// A unified patch of an edit: what the diff view of the edit block is fed.
export function editPatch(path: string, args: Record<string, unknown>, firstLine: number | null): string {
  const rows: DiffRow[] = diffLines(str(args.old_text), str(args.new_text), firstLine ?? 1, HUNK_CONTEXT)
  if (!rows.length) return ""
  const oldCount = rows.filter((row) => row.kind !== "add").length
  const newCount = rows.filter((row) => row.kind !== "del").length
  const first = rows[0]!
  const oldStart = first.oldNo ?? (first.newNo ?? 1)
  const newStart = first.newNo ?? oldStart
  const body = rows.map((row) => `${row.kind === "add" ? "+" : row.kind === "del" ? "-" : " "}${row.text}`)
  return [`--- a/${path}`, `+++ b/${path}`, `@@ -${oldStart},${oldCount} +${newStart},${newCount} @@`, ...body].join("\n") + "\n"
}

// Extension → tree-sitter filetype OpenTUI knows; anything else renders as plain text.
export function filetypeOf(file: string): string | undefined {
  const extension = file.split(".").at(-1)?.toLowerCase()
  switch (extension) {
    case "ts": return "typescript"
    case "tsx": return "typescriptreact"
    case "js": case "mjs": case "cjs": case "jsx": return "javascript"
    case "md": return "markdown"
    case "zig": return "zig"
    default: return undefined
  }
}
