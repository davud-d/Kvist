import type { ComponentChildren } from "preact"

import { diffLines, toLines } from "../../../tui/ink/diff"
import { formatThought, outputLines } from "../../../tui/ink/format"
import { actionTarget, type Item } from "../../../tui/ink/state"
import { Icon, type IconName } from "./icons"
import { CodeBlock, Failure, OUTPUT_LINES, Output, commandLine, diffLineKind, diffRows, fileLines, tailLines, type CodeLine } from "./transcript"
import { verbOf } from "./verbs"

type ToolItem = Extract<Item, { kind: "tool" }>
export type ActivityItem = Extract<Item, { kind: "step" | "tool" | "thought" | "explored" }>

// Work the agent does between two things it says: these fold into one small block.
export const isActivity = (item: Item): item is ActivityItem =>
  item.kind === "step" || item.kind === "tool" || item.kind === "thought" || item.kind === "explored"

type Category = "read" | "search" | "list" | "edit" | "run" | "other"

const CATEGORY: Record<string, Category> = {
  read_file: "read",
  read_multiple_files: "read",
  search_code: "search",
  find_files: "search",
  web_search: "search",
  list_dir: "list",
  list_tree: "list",
  write_file: "edit",
  append_file: "edit",
  replace_in_file: "edit",
  run_shell: "run",
  git_status: "run",
  git_diff: "run",
}

const ICON: Record<Category, IconName> = {
  read: "arrowRight",
  search: "arrowRight",
  list: "arrowRight",
  edit: "arrowUpRight",
  run: "terminal",
  other: "arrowRight",
}

const LONG_COMMAND = 72

interface Row {
  key: string
  category: Category
  // How many things the row stands for: a read of three files counts three.
  weight: number
  // Edits of one file count once in the summary.
  path: string
  icon: IconName
  verb: string
  subject: ComponentChildren
  meta: ComponentChildren
  body: ComponentChildren
  failed: boolean
}

const text = (value: unknown): string => (typeof value === "string" ? value : "")

function categoryOf(action: string): Category {
  return CATEGORY[action] ?? "other"
}

function Name({ path }: { path: string }) {
  const normalized = path.replaceAll("\\", "/")
  const cut = normalized.lastIndexOf("/")
  return (
    <>
      <span class="act-name">{normalized.slice(cut + 1)}</span>
      {cut >= 0 ? <span class="act-dir">{normalized.slice(0, cut + 1)}</span> : null}
    </>
  )
}

function subjectOf(action: string, args: Record<string, unknown>, detail: string): ComponentChildren {
  if (action === "read_multiple_files" && Array.isArray(args.paths) && args.paths.length > 0) {
    const paths = args.paths.map(String)
    return <><Name path={paths[0]} />{paths.length > 1 ? <span class="act-dir">and {paths.length - 1} more</span> : null}</>
  }
  if (action === "search_code" || action === "find_files" || action === "web_search") return <span class="act-query">{detail}</span>
  // A background command is named by an id nobody typed; the verb already says which kind of step this is.
  if (action === "job_output" || action === "job_stop") return null
  return <Name path={detail} />
}

const DETAIL_IS_SUMMARY = new Set(["web_search", "fetch_url"])

function failedRun(item: ToolItem): boolean {
  const { result } = item
  return !result.ok || (result.exit_code !== null && result.exit_code !== 0)
}

function shellRow(item: ToolItem): Row {
  const { result, args } = item
  const command = commandLine(args)
  const { shown, earlier } = tailLines(result.output, OUTPUT_LINES)
  const code = result.exit_code ?? null
  const failed = failedRun(item)
  const body = (
    <>
      {command.length > LONG_COMMAND ? <div class="act-cmd">{command}</div> : null}
      {shown.length > 0 ? <pre class="act-out">{earlier > 0 ? `… ${earlier} earlier lines\n` : ""}{shown.join("\n")}</pre> : null}
      {code === null && !result.ok ? <div class="act-fail">{result.summary}</div> : null}
    </>
  )
  const hasBody = command.length > LONG_COMMAND || shown.length > 0 || (code === null && !result.ok)
  return {
    key: `tool-${item.id}`, category: "run", weight: 1, path: "", icon: "terminal", verb: "",
    subject: <span class="act-command" title={command}>{command}</span>,
    meta: code !== null && code !== 0 ? <span class="act-chip bad" title={`exit ${code}`}>failed</span> : null,
    body: hasBody ? body : null,
    failed,
  }
}

function gitDiffRow(item: ToolItem): Row {
  const { result } = item
  const { lines, more } = diffLinesOf(result.output)
  const target = actionTarget(item.args)
  return {
    key: `tool-${item.id}`, category: "run", weight: 1, path: "", icon: "terminal", verb: "",
    subject: <span class="act-command">{`git diff${target ? ` ${target}` : ""}`}</span>,
    meta: result.ok && lines.length === 0 ? <span class="act-meta-text">no changes</span> : null,
    body: !result.ok ? <Failure result={result} /> : lines.length > 0 ? <CodeBlock lines={lines} more={more} /> : null,
    failed: !result.ok,
  }
}

const GIT_DIFF_ROWS = OUTPUT_LINES * 2

function diffLinesOf(output: string): { lines: CodeLine[]; more: number } {
  const all = outputLines(output)
  const shown = all.slice(0, GIT_DIFF_ROWS)
  const lines = shown.map((line): CodeLine => {
    const kind = diffLineKind(line)
    return { no: null, sign: "", text: line, kind: kind === "add" || kind === "del" ? kind : kind === "meta" ? "ctx" : "plain" }
  })
  return { lines, more: all.length - shown.length }
}

function writeRow(item: ToolItem, verb: string, sign: string, kind: "plain" | "add"): Row {
  const { result, args } = item
  const path = result.path ?? text(args.path)
  const content = text(args.content)
  const { lines, more } = fileLines(content, result.first_line ?? 1, sign, kind)
  return {
    key: `tool-${item.id}`, category: "edit", weight: 1, path, icon: "arrowUpRight", verb,
    subject: <Name path={path} />,
    meta: result.ok ? <span class="act-meta-text">{toLines(content).length} lines</span> : null,
    body: result.ok ? <CodeBlock lines={lines} more={more} /> : <Failure result={result} />,
    failed: !result.ok,
  }
}

function editRow(item: ToolItem): Row {
  const { result, args } = item
  const path = result.path ?? text(args.path)
  const rows = result.ok ? diffLines(text(args.old_text), text(args.new_text), result.first_line ?? 1) : []
  const added = rows.filter((row) => row.kind === "add").length
  const removed = rows.filter((row) => row.kind === "del").length
  const { lines, more } = diffRows(rows)
  const count = typeof args.count === "number" ? args.count : 1
  return {
    key: `tool-${item.id}`, category: "edit", weight: 1, path, icon: "arrowUpRight", verb: "Edited",
    subject: <Name path={path} />,
    meta: result.ok ? <span class="act-meta-text"><span class="plus">+{added}</span> <span class="minus">−{removed}</span></span> : null,
    body: result.ok
      ? <><CodeBlock lines={lines} more={more} />{count > 1 ? <div class="act-note">{result.summary}</div> : null}</>
      : <Failure result={result} />,
    failed: !result.ok,
  }
}

function lineRow(item: ToolItem): Row {
  const { result, action, args } = item
  const detail = DETAIL_IS_SUMMARY.has(action) ? result.summary : actionTarget(args)
  const category = categoryOf(action)
  const weight = action === "read_multiple_files" && Array.isArray(args.paths) && args.paths.length > 0 ? args.paths.length : 1
  const body = !result.ok ? <Failure result={result} /> : action === "delegate_task" && result.output.trim() ? <Output text={result.output} /> : null
  return {
    key: `tool-${item.id}`, category, weight, path: "", icon: ICON[category], verb: verbOf(action),
    subject: subjectOf(action, args, detail),
    meta: result.ok && !DETAIL_IS_SUMMARY.has(action) && !action.startsWith("job_") && result.summary ? <span class="act-meta-text">{result.summary}</span> : null,
    body,
    failed: !result.ok,
  }
}

function toolRow(item: ToolItem): Row {
  switch (item.action) {
    case "write_file": return writeRow(item, "Wrote", "", "plain")
    case "append_file": return writeRow(item, "Appended", "+", "add")
    case "replace_in_file": return editRow(item)
    case "run_shell": return shellRow(item)
    case "git_diff": return gitDiffRow(item)
    default: return lineRow(item)
  }
}

// An explored label is "action  target", as the reducer builds it.
function exploredRows(item: Extract<Item, { kind: "explored" }>): Row[] {
  return item.names.map((label, index) => {
    const [action = "", ...rest] = label.split(/\s{2,}/)
    const target = rest.join("  ")
    const category = categoryOf(action)
    return {
      key: `explored-${item.id}-${index}`, category, weight: 1, path: "", icon: ICON[category], verb: verbOf(action),
      subject: subjectOf(action, {}, target), meta: null, body: null, failed: false,
    }
  })
}

function rowsOf(item: ActivityItem): Row[] {
  switch (item.kind) {
    case "tool": return [toolRow(item)]
    case "explored": return exploredRows(item)
    case "step": {
      const category = categoryOf(item.action)
      return [{
        key: `step-${item.id}`, category, weight: 1, path: "", icon: ICON[category], verb: verbOf(item.action),
        subject: subjectOf(item.action, {}, item.target), meta: null, body: null, failed: false,
      }]
    }
    case "thought": return []
  }
}

const PHRASES: [Category, (n: number) => string][] = [
  ["read", (n) => `read ${n} ${n === 1 ? "file" : "files"}`],
  ["search", (n) => (n === 1 ? "searched once" : `searched ${n} times`)],
  ["list", (n) => `listed ${n} ${n === 1 ? "folder" : "folders"}`],
  ["edit", (n) => `edited ${n} ${n === 1 ? "file" : "files"}`],
  ["run", (n) => `ran ${n} ${n === 1 ? "command" : "commands"}`],
  ["other", (n) => `used ${n} other ${n === 1 ? "tool" : "tools"}`],
]

// "Read 3 files, edited 2 files, ran 1 command": it grows as the work does.
export function summarize(rows: Row[]): string {
  const counts = new Map<Category, number>()
  for (const row of rows) {
    if (row.category === "edit") continue
    counts.set(row.category, (counts.get(row.category) ?? 0) + row.weight)
  }
  const edited = new Set(rows.filter((row) => row.category === "edit").map((row) => row.path || row.key))
  if (edited.size > 0) counts.set("edit", edited.size)
  const parts = PHRASES.filter(([category]) => counts.has(category)).map(([category, phrase]) => phrase(counts.get(category) ?? 0))
  const line = parts.join(", ")
  return line.charAt(0).toUpperCase() + line.slice(1)
}

function Chevron() {
  return <span class="act-chev"><Icon name="chevron" size={11} /></span>
}

function RowHead({ row }: { row: Row }) {
  return (
    <>
      <span class={`act-glyph ${row.failed ? "bad" : ""}`}><Icon name={row.icon} size={12} /></span>
      {row.verb ? <span class="act-verb">{row.verb}</span> : null}
      <span class="act-subject">{row.subject}</span>
      {row.meta ? <span class="act-meta">{row.meta}</span> : null}
    </>
  )
}

function RowView({ row }: { row: Row }) {
  if (!row.body) return <div class="act-row"><div class="act-line"><RowHead row={row} /></div></div>
  return (
    <details class="act-row">
      <summary class="act-line"><RowHead row={row} /><Chevron /></summary>
      <div class="act-body">{row.body}</div>
    </details>
  )
}

function Thoughts({ thoughts }: { thoughts: Extract<Item, { kind: "thought" }>[] }) {
  const written = thoughts.filter((thought) => thought.text.trim().length > 0)
  if (written.length === 0) return null
  return <>{written.map((thought) => <div key={thought.id} class="act-thought">{thought.text}</div>)}</>
}

// A run of the agent's work as one quiet line. One action shows as itself, several as a
// count that grows; a click opens what is behind it.
export function ActivityGroup({ items, live }: { items: ActivityItem[]; live: boolean }) {
  const thoughts = items.filter((item): item is Extract<Item, { kind: "thought" }> => item.kind === "thought")
  const rows = items.flatMap(rowsOf)
  const failed = rows.filter((row) => row.failed).length
  const seconds = thoughts.reduce((sum, thought) => sum + thought.seconds, 0)
  const hasThoughts = thoughts.some((thought) => thought.text.trim().length > 0)

  if (rows.length === 0) {
    const label = formatThought(seconds).replace(":", " for")
    return (
      <details class={`act ${live ? "live" : ""}`}>
        <summary class="act-line"><span class="act-summary">{label}</span>{hasThoughts ? <Chevron /> : null}</summary>
        <div class="act-body"><Thoughts thoughts={thoughts} /></div>
      </details>
    )
  }
  if (rows.length === 1) {
    const [row] = rows
    return (
      <details class={`act ${live ? "live" : ""}`}>
        <summary class="act-line"><RowHead row={row} />{row.body || hasThoughts ? <Chevron /> : null}</summary>
        <div class="act-body"><Thoughts thoughts={thoughts} />{row.body}</div>
      </details>
    )
  }
  return (
    <details class={`act ${live ? "live" : ""}`}>
      <summary class="act-line">
        {live ? <span class="act-pulse" /> : null}
        <span class="act-summary">{summarize(rows)}</span>
        {failed > 0 ? <span class="act-chip bad">{failed} failed</span> : null}
        <Chevron />
      </summary>
      <div class="act-body">
        <Thoughts thoughts={thoughts} />
        <div class="act-list">{rows.map((row) => <RowView key={row.key} row={row} />)}</div>
      </div>
    </details>
  )
}
