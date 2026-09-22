import { diffLines, toLines } from "../../src/diff"
import { clipLines, formatThought, outputLines } from "../../src/format"
import { actionTarget, exploreSummary, type Item } from "../../src/state"
import { verbOf } from "./verbs"

// Caps mirror what transcript.tsx already shows on screen, so a copied turn never
// dumps more than what was visible to copy in the first place.
const WRITE_LINES = 12
const DIFF_ROWS = 30
const OUTPUT_LINES = 15

// The items of the turn a given answer belongs to: back to the nearest preceding
// "user" item (a turn's own request), inclusive of both ends. Falls back to the
// start of the feed if none is found (e.g. a redrawn turn with no request line).
export function turnSlice(items: Item[], answerId: number): Item[] {
  const end = items.findIndex((item) => item.id === answerId)
  if (end === -1) return []
  let start = 0
  for (let i = end - 1; i >= 0; i--) {
    if (items[i]?.kind === "user") {
      start = i
      break
    }
  }
  return items.slice(start, end + 1)
}

const str = (value: unknown): string => (typeof value === "string" ? value : "")

function commandLine(args: Record<string, unknown>): string {
  const command = args.command
  return Array.isArray(command) ? command.map(String).join(" ") : str(command)
}

function capped(lines: string[], max: number): string {
  const { shown, more } = clipLines(lines, max)
  return more > 0 ? [...shown, `… ${more} more lines`].join("\n") : shown.join("\n")
}

function fence(lang: string, body: string): string {
  return body.trim() ? `\`\`\`${lang}\n${body}\n\`\`\`` : ""
}

function writeText(item: Extract<Item, { kind: "tool" }>, verb: string, sign: string): string {
  const { result, args } = item
  const path = result.path ?? str(args.path)
  const lines = toLines(str(args.content)).map((line) => `${sign}${line}`)
  const head = `**${verb} \`${path}\`**`
  if (!result.ok) return `${head}\n✗ ${result.summary}`
  return `${head}\n${fence("diff", capped(lines, WRITE_LINES))}`
}

function editText(item: Extract<Item, { kind: "tool" }>): string {
  const { result, args } = item
  const path = result.path ?? str(args.path)
  const head = `**Edited \`${path}\`**`
  if (!result.ok) return `${head}\n✗ ${result.summary}`
  const rows = diffLines(str(args.old_text), str(args.new_text), result.first_line ?? 1)
  const lines = rows.map((row) => `${row.kind === "add" ? "+" : row.kind === "del" ? "-" : " "}${row.text}`)
  return `${head}\n${fence("diff", capped(lines, DIFF_ROWS))}`
}

function shellText(item: Extract<Item, { kind: "tool" }>): string {
  const { result, args } = item
  const code = result.exit_code
  const head = `$ ${commandLine(args)}`
  const body = capped(outputLines(result.output), OUTPUT_LINES)
  const tail = code !== null ? `\nexit ${code}` : result.ok ? "" : `\n✗ ${result.summary}`
  return `${fence("", body ? `${head}\n${body}` : head)}${tail}`
}

function toolText(item: Extract<Item, { kind: "tool" }>): string {
  switch (item.action) {
    case "write_file":
      return writeText(item, "Wrote", "")
    case "append_file":
      return writeText(item, "Appended", "+")
    case "replace_in_file":
      return editText(item)
    case "run_shell":
      return shellText(item)
    case "git_diff": {
      const body = capped(outputLines(item.result.output), OUTPUT_LINES * 2)
      return body ? fence("diff", body) : "No changes"
    }
    default: {
      const detail = actionTarget(item.args)
      const line = `${verbOf(item.action)}${detail ? ` ${detail}` : ""}`
      return item.result.ok ? line : `${line}\n✗ ${item.result.summary}`
    }
  }
}

function itemText(item: Item): string {
  switch (item.kind) {
    case "banner":
      return ""
    case "user":
      return `**You:** ${item.text}`
    case "plan": {
      const lines = [`**Plan** (${item.complexity})`, ...item.steps.map((step, i) => `${i + 1}. ${step}`)]
      if (item.risks.length > 0) lines.push(`Risks: ${item.risks.join("; ")}`)
      return lines.join("\n")
    }
    case "step":
      return `${verbOf(item.action)}${item.target ? ` ${item.target}` : ""} (no result)`
    case "tool":
      return toolText(item)
    case "thought":
      // formatThought already reads "Thought" or "Thought: 2.4s"; an em dash separates
      // it from the note instead of a second colon.
      return item.text ? `_${formatThought(item.seconds)}_ — ${item.text}` : `_${formatThought(item.seconds)}_`
    case "explored":
      return `Explored: ${exploreSummary(item.counts)}`
    case "say":
      return `_${item.text}_`
    case "answer":
      return item.text.trim()
    case "note":
      return item.tone === "debug" ? "" : item.text
  }
}

// The turn as readable Markdown: what was asked, what the agent did, and its answer.
export function turnToText(items: Item[]): string {
  return items
    .map(itemText)
    .filter((text) => text.trim() !== "")
    .join("\n\n")
}

export function answerToText(item: Extract<Item, { kind: "answer" }>): string {
  return item.text.trim()
}
