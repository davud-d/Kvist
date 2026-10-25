import { useEffect, useRef, useState } from "preact/hooks"

import { diffLines, numberWidth, toLines, type DiffRow } from "../../../tui/ink/diff"
import { clipLines, formatSeconds, formatThought, lastLines, outputLines } from "../../../tui/ink/format"
import { actionTarget, exploreSummary, type ActionResult, type Item } from "../../../tui/ink/state"
import { post } from "./host"
import { Icon } from "./icons"
import { FileIcon } from "./file-icons"
import { Markdown } from "./markdown"
import { turnSlice, turnToText } from "./export"
import { classifyRun, worseOf, type Tone } from "./outcome"
import { jobFailed, jobLabel, jobsCopy } from "./jobs"
import { verbOf } from "./verbs"

type ToolItem = Extract<Item, { kind: "tool" }>

export const OUTPUT_LINES = 15
const WRITE_LINES = 12
const DIFF_ROWS = 30
// How long a finished thought stays open before it folds itself away.
const FOLD_AFTER_MS = 1400
export const HIDDEN_STEPS = new Set(["final_answer", "report_to_user", "refuse"])

const text = (value: unknown): string => (typeof value === "string" ? value : "")

// The action names are the core's vocabulary; a person reads what was done.
export const FILE_SUBJECT = new Set(["read_file", "write_file", "append_file", "replace_in_file"])

// The file name carries the meaning, so it leads and its folder recedes.
function FilePath({ path, open }: { path: string; open?: number | null }) {
  const normalized = path.replaceAll("\\", "/")
  const cut = normalized.lastIndexOf("/")
  const name = normalized.slice(cut + 1)
  const dir = cut >= 0 ? normalized.slice(0, cut + 1) : ""
  const body = (
    <>
      <FileIcon path={path} /><span class="file-name">{name}</span>
      {dir ? <span class="file-dir">{dir}</span> : null}
    </>
  )
  if (open === undefined) return <span class="file">{body}</span>
  return (
    <button class="file" title={`Open ${path}`} onClick={(event) => { event.preventDefault(); post({ type: "openFile", path, line: open }) }}>
      {body}
    </button>
  )
}

function Subject({ action, args, detail }: { action: string; args: Record<string, unknown>; detail: string }) {
  if (FILE_SUBJECT.has(action) && text(args.path)) return <FilePath path={text(args.path)} open={null} />
  if (action === "read_multiple_files" && Array.isArray(args.paths)) {
    const paths = args.paths.map(String)
    return <span class="file"><FileIcon path={paths[0]} /><span class="file-name">{paths[0]}</span><span class="file-dir">{paths.length > 1 ? ` and ${paths.length - 1} more` : ""}</span></span>
  }
  if (action === "search_code" || action === "find_files") return <span class="query"><FileIcon action={action} />{detail}</span>
  return <span class="file"><FileIcon action={action} path={detail} /><span class="file-name">{detail}</span></span>
}

export function commandLine(args: Record<string, unknown>): string {
  const command = args.command
  return Array.isArray(command) ? command.map(String).join(" ") : text(command)
}

function Node({ kind }: { kind: "ok" | "warn" | "bad" | "idle" | "run" }) {
  return (
    <span class={`node ${kind}`}>
      {kind === "ok" || kind === "warn" ? <Icon name="check" size={12} /> : kind === "bad" ? <Icon name="cross" size={12} /> : null}
    </span>
  )
}

export function Output({ text: value }: { text: string }) {
  const { shown, more } = clipLines(outputLines(value), OUTPUT_LINES)
  if (shown.length === 0) return null
  return (
    <pre class="output">
      {shown.join("\n")}
      {more > 0 ? `\n… ${more} more lines` : ""}
    </pre>
  )
}

export function Failure({ result }: { result: ActionResult }) {
  const detail = result.output.trim()
  return (
    <>
      <div class="failure">{result.summary}</div>
      {detail && !result.summary.includes(detail) ? <Output text={result.output} /> : null}
    </>
  )
}

export interface CodeLine {
  no: number | null
  sign: string
  text: string
  kind: "ctx" | "add" | "del" | "plain"
}

export function CodeBlock({ lines, more }: { lines: CodeLine[]; more: number }) {
  const width = String(Math.max(1, ...lines.map((line) => line.no ?? 0))).length
  return (
    <div class="code" style={{ "--nw": `${width}ch` }}>
      {lines.map((line, i) => (
        <div key={i} class={`code-row ${line.kind}`}>
          <span class="gutter">{line.no ?? ""}</span>
          <span class="sign">{line.sign}</span>
          <span class="code-text">{line.text || " "}</span>
        </div>
      ))}
      {more > 0 ? <div class="code-more">… {more} more lines</div> : null}
    </div>
  )
}

export function fileLines(content: string, first: number, sign: string, kind: CodeLine["kind"]): { lines: CodeLine[]; more: number } {
  const all = toLines(content)
  const shown = all.slice(0, WRITE_LINES)
  return { lines: shown.map((line, i) => ({ no: first + i, sign, text: line, kind })), more: all.length - shown.length }
}

export function diffRows(rows: DiffRow[]): { lines: CodeLine[]; more: number } {
  const shown = rows.slice(0, DIFF_ROWS)
  const lines = shown.map((row): CodeLine =>
    row.kind === "ctx" ? { no: row.newNo, sign: " ", text: row.text, kind: "ctx" }
    : row.kind === "del" ? { no: row.oldNo, sign: "−", text: row.text, kind: "del" }
    : { no: row.newNo, sign: "+", text: row.text, kind: "add" })
  return { lines, more: rows.length - shown.length }
}

function Head({ result, verb, path, children }: { result: ActionResult; verb: string; path: string; children?: preact.ComponentChildren }) {
  return (
    <summary>
      <Node kind={result.ok ? "ok" : "bad"} />
      <span class="verb">{verb}</span>
      <span class="grow"><FilePath path={path} open={result.ok ? result.first_line : undefined} /></span>
      {children}
    </summary>
  )
}

function WriteCard({ item, verb, sign, kind }: { item: ToolItem; verb: string; sign: string; kind: CodeLine["kind"] }) {
  const { result, args } = item
  const path = result.path ?? text(args.path)
  const content = text(args.content)
  const { lines, more } = fileLines(content, result.first_line ?? 1, sign, kind)
  return (
    <details class="work" open>
      <Head result={result} verb={verb} path={path}>
        {result.ok ? <span class="count">{toLines(content).length} lines</span> : null}
      </Head>
      {result.ok ? <CodeBlock lines={lines} more={more} /> : <Failure result={result} />}
    </details>
  )
}

function EditCard({ item }: { item: ToolItem }) {
  const { result, args } = item
  const path = result.path ?? text(args.path)
  const rows = result.ok ? diffLines(text(args.old_text), text(args.new_text), result.first_line ?? 1) : []
  const added = rows.filter((row) => row.kind === "add").length
  const removed = rows.filter((row) => row.kind === "del").length
  const { lines, more } = diffRows(rows)
  const count = typeof args.count === "number" ? args.count : 1
  return (
    <details class="work" open>
      <Head result={result} verb="Edited" path={path}>
        {result.ok ? <span class="count"><span class="plus">+{added}</span> <span class="minus">−{removed}</span></span> : null}
      </Head>
      {result.ok ? <CodeBlock lines={lines} more={more} /> : <Failure result={result} />}
      {result.ok && count > 1 ? <div class="summary">{result.summary}</div> : null}
    </details>
  )
}

type RingPhase = "idle" | "run" | "finish" | "done"

// A command that ends within this time needs no ceremony; a longer one gets a light around it.
const LONG_RUN_MS = 2000
// The finishing animation takes a second, then the ring stays a moment and fades.
const FINISH_MS = 1000
const HOLD_MS = 700
const LIVE_LINES = 6

export function tailLines(value: string, count: number): { shown: string[]; earlier: number } {
  const lines = outputLines(value)
  return { shown: lines.slice(-count), earlier: Math.max(0, lines.length - count) }
}

interface ShellProps {
  args: Record<string, unknown>
  result: ActionResult | null
  // When the command started; 0 means it is not being watched live, so no ring.
  startedAt: number
  live: string
}

// One component for the running and the finished command, so the ring that was
// circling keeps its element and hands over to the finishing animation.
export function ShellCard({ args, result, startedAt, live }: ShellProps) {
  const [phase, setPhase] = useState<RingPhase>("idle")
  const worst = useRef<Tone>("ok")
  const finished = result !== null
  // What the live output has shown so far. The tail is short, so an error that has scrolled
  // out of it must not turn the light green again: the worst tone seen is kept.
  worst.current = worseOf(worst.current, classifyRun({ ok: true, exit_code: 0, output: live }))
  const tone = result ? worseOf(worst.current, classifyRun(result)) : worst.current

  useEffect(() => {
    if (finished || startedAt === 0) return
    const timer = setTimeout(() => setPhase("run"), Math.max(0, LONG_RUN_MS - (Date.now() - startedAt)))
    return () => clearTimeout(timer)
  }, [finished])

  useEffect(() => {
    if (!finished || phase !== "run") return
    setPhase("finish")
    const timer = setTimeout(() => setPhase("done"), FINISH_MS + HOLD_MS)
    return () => clearTimeout(timer)
  }, [finished])

  const { shown, earlier } = tailLines(result ? result.output : live, result ? OUTPUT_LINES : LIVE_LINES)
  const code = result?.exit_code ?? null
  return (
    <div class="work">
      <Node kind={!result ? "run" : tone} />
      <div class={`term tone-${tone} ${phase === "idle" ? "" : `ring-${phase}`}`}>
        <div class="term-head">
          <span class="prompt">{document.getElementById("root")?.dataset.layout === "workspace" ? <FileIcon action="run_shell" /> : "$"}</span>
          <span class="term-cmd">{commandLine(args)}</span>
          {code !== null ? <span class={`exit ${tone}`}>exit {code}</span> : null}
        </div>
        {shown.length > 0 ? (
          <pre class="term-out">
            {earlier > 0 ? `… ${earlier} earlier lines\n` : ""}
            {shown.join("\n")}
          </pre>
        ) : null}
        {result && shown.length === 0 && result.ok ? <div class="term-empty">No output</div> : null}
        {result && !result.ok && code === null ? <div class="failure">{result.summary}</div> : null}
      </div>
    </div>
  )
}

// A background job that ended. The running ones live in the strip above the composer.
function JobCard({ item }: { item: Extract<Item, { kind: "job" }> }) {
  const copy = jobsCopy(item.russian)
  const failed = jobFailed(item.status, item.exitCode)
  const { shown, earlier } = tailLines(item.output, OUTPUT_LINES)
  return (
    <div class="work">
      <Node kind={failed ? "bad" : "ok"} />
      <div class={`term tone-${failed ? "bad" : "ok"}`}>
        <div class="term-head">
          <span class="prompt">{document.getElementById("root")?.dataset.layout === "workspace" ? <FileIcon action="run_shell" /> : "$"}</span>
          <span class="term-cmd" title={item.command.join(" ")}>{jobLabel(item)}</span>
          <span class="exit">{copy.background}</span>
          <span class={`exit ${failed ? "bad" : ""}`}>{copy.ended(item.status, item.exitCode)} · {formatSeconds(item.seconds)}</span>
        </div>
        {shown.length > 0 ? (
          <pre class="term-out">
            {earlier > 0 ? `${copy.earlier(earlier)}\n` : ""}
            {shown.join("\n")}
          </pre>
        ) : null}
      </div>
    </div>
  )
}

export function diffLineKind(line: string): string {
  if (/^(diff --git|index |--- |\+\+\+ |@@)/.test(line)) return "meta"
  if (line.startsWith("+")) return "add"
  if (line.startsWith("-")) return "del"
  return ""
}

function GitDiffCard({ item }: { item: ToolItem }) {
  const { result } = item
  const { shown, more } = clipLines(outputLines(result.output), OUTPUT_LINES * 2)
  const target = actionTarget(item.args)
  return (
    <details class="work" open={shown.length > 0}>
      <summary>
        <Node kind={result.ok ? "ok" : "bad"} />
        <span class="prompt">{document.getElementById("root")?.dataset.layout === "workspace" ? <FileIcon action="run_shell" /> : "$"}</span>
        <span class="target cmd">git diff{target ? ` ${target}` : ""}</span>
      </summary>
      {!result.ok ? <Failure result={result} /> : null}
      {result.ok && shown.length === 0 ? <div class="summary">No changes</div> : null}
      {shown.length > 0 ? (
        <div class="code">
          {shown.map((line, i) => <div key={i} class={`code-row ${diffLineKind(line)}`}><span class="code-text">{line || " "}</span></div>)}
          {more > 0 ? <div class="code-more">… {more} more lines</div> : null}
        </div>
      ) : null}
    </details>
  )
}

const DETAIL_IS_SUMMARY = new Set(["web_search", "fetch_url"])

function LineCard({ item }: { item: ToolItem }) {
  const { result, action, args } = item
  const detail = DETAIL_IS_SUMMARY.has(action) ? result.summary : actionTarget(args)
  const body = !result.ok ? <Failure result={result} /> : action === "delegate_task" ? <Output text={result.output} /> : null
  return (
    <details class="work" open={!result.ok}>
      <summary>
        <Node kind={result.ok ? "ok" : "bad"} />
        <span class="verb">{verbOf(action)}</span>
        <span class="grow"><Subject action={action} args={args} detail={detail} /></span>
        {result.ok && !DETAIL_IS_SUMMARY.has(action) && result.summary ? <span class="count">{result.summary}</span> : null}
      </summary>
      {body}
    </details>
  )
}

function Tool({ item }: { item: ToolItem }) {
  switch (item.action) {
    case "write_file":
      return <WriteCard item={item} verb="Wrote" sign="" kind="plain" />
    case "append_file":
      return <WriteCard item={item} verb="Appended" sign="+" kind="add" />
    case "replace_in_file":
      return <EditCard item={item} />
    case "run_shell":
      return <ShellCard args={item.args} result={item.result} startedAt={0} live="" />
    case "git_diff":
      return <GitDiffCard item={item} />
    default:
      return <LineCard item={item} />
  }
}

// Thoughts that already folded once stay folded when the panel is redrawn.
const folded = new Set<string>()

function Thought({ item, uid, settled }: { item: Extract<Item, { kind: "thought" }>; uid: string; settled: boolean }) {
  const hasText = item.text.trim().length > 0
  // A thought redrawn from history has nothing to fold: it starts closed.
  const [open, setOpen] = useState(hasText && !settled && !folded.has(uid))
  const touched = useRef(false)

  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => {
      if (touched.current) return
      folded.add(uid)
      setOpen(false)
    }, FOLD_AFTER_MS)
    return () => clearTimeout(timer)
  }, [])

  const label = formatThought(item.seconds).replace(":", " for")
  if (!hasText) {
    return (
      <div class="work quiet">
        <Node kind="idle" />
        {label}
      </div>
    )
  }
  return (
    <div class={`work quiet fold ${open ? "open" : ""}`}>
      <button class="fold-head" onClick={() => { touched.current = true; setOpen(!open) }}>
        <Node kind="idle" />
        <span>{label}</span>
        <span class="fold-chevron"><Icon name="chevron" size={14} /></span>
      </button>
      <div class="fold-body">
        <div class="fold-inner">
          <div class="thought-text">{item.text}</div>
        </div>
      </div>
    </div>
  )
}

// The thought as it is written: the newest lines stay in view, and the header counts the seconds.
export function Thinking({ reasoning, since }: { reasoning: string; since: number | null }) {
  const [now, setNow] = useState(Date.now())
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    const element = body.current
    if (!element) return
    element.scrollTo({ top: element.scrollHeight })
    // The fade at the top only makes sense once older lines have scrolled out of view.
    element.classList.toggle("overflowing", element.scrollHeight > element.clientHeight)
  }, [reasoning])
  return (
    <div class="work live thinking">
      <span class="node run" />
      <div class="thinking-head">
        <span class="shimmer">Thinking</span>
        {since ? <span class="thinking-time">{formatSeconds((now - since) / 1000)}</span> : null}
      </div>
      {reasoning ? <div class="thinking-body" ref={body}>{lastLines(reasoning, 40)}</div> : null}
    </div>
  )
}

export function Working({ label, target, action = "list_dir", hint = "" }: { label: string; target: string; action?: string; hint?: string }) {
  return (
    <div class="work live">
      <span class="node run" />
      <span class="verb">{label}</span>
      {target ? <span class="file"><FileIcon action={action} path={target} /><span class="file-name">{target}</span></span> : null}
      {hint ? <span class="live-hint">{hint}</span> : null}
    </div>
  )
}

// A label is "action  target", as the reducer builds it.
function ExploredName({ label }: { label: string }) {
  const [action = "", ...rest] = label.split(/\s{2,}/)
  const target = rest.join("  ")
  return (
    <>
      <span class="verb">{verbOf(action)}</span>
      {FILE_SUBJECT.has(action) && target ? <FilePath path={target} /> : <span class="file"><FileIcon action={action} path={target} /><span class="file-name">{target}</span></span>}
    </>
  )
}

function Explored({ item }: { item: Extract<Item, { kind: "explored" }> }) {
  const names = item.names ?? []
  if (names.length === 0) {
    return (
      <div class="work quiet">
        <Node kind="idle" />
        Explored {exploreSummary(item.counts)}
      </div>
    )
  }
  return (
    <details class="work quiet">
      <summary>
        <Node kind="idle" />
        <span>Explored {exploreSummary(item.counts)}</span>
        <span class="fold-chevron"><Icon name="chevron" size={14} /></span>
      </summary>
      <ul class="names">{names.map((name, i) => <li key={i}><ExploredName label={name} /></li>)}</ul>
    </details>
  )
}

// How fast letters appear, and the ceiling so a long line does not take forever.
const TYPE_MS_PER_CHAR = 12
const TYPE_MAX_MS = 500

// The model's one-line comment, typed out before the action it describes settles in
// (the action's own card gets a short matching entrance delay in CSS). A line redrawn
// from history appears whole: only fresh narration is worth watching type itself out.
function Say({ text: full, settled }: { text: string; settled: boolean }) {
  const [shown, setShown] = useState(settled ? full : "")
  useEffect(() => {
    if (settled) return
    const step = Math.max(1, Math.round(full.length / (TYPE_MAX_MS / TYPE_MS_PER_CHAR)))
    const timer = setInterval(() => {
      setShown((current) => {
        const next = full.slice(0, current.length + step)
        if (next.length >= full.length) clearInterval(timer)
        return next
      })
    }, TYPE_MS_PER_CHAR)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <div class="work quiet say">
      <Node kind="idle" />
      {shown}
    </div>
  )
}

// How long the button shows a checkmark after a successful copy.
const COPIED_HOLD_MS = 1200

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Some webview states (no focus) reject the async API; the legacy path still works there.
    const field = document.createElement("textarea")
    field.value = text
    field.style.position = "fixed"
    field.style.opacity = "0"
    document.body.appendChild(field)
    field.select()
    const ok = document.execCommand("copy")
    field.remove()
    return ok
  }
}

// Bottom-right of the answer: a quiet icon that opens a two-line choice between the
// answer alone and everything the agent did to produce it.
function CopyMenu({ answerText, turnText }: { answerText: string; turnText: () => string }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onOutside = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onOutside)
    return () => document.removeEventListener("mousedown", onOutside)
  }, [open])

  async function pick(text: string): Promise<void> {
    setOpen(false)
    if (await copy(text)) {
      setCopied(true)
      setTimeout(() => setCopied(false), COPIED_HOLD_MS)
    }
  }

  return (
    <div class="copy-menu" ref={root}>
      <button class="icon" title="Copy" onClick={() => setOpen(!open)}>
        <Icon name={copied ? "check" : "copy"} />
      </button>
      {open ? (
        <div class="copy-options">
          <button onClick={() => pick(answerText)}>Copy answer</button>
          <button onClick={() => pick(turnText())}>Copy full turn</button>
        </div>
      ) : null}
    </div>
  )
}

function Answer({ item, items }: { item: Extract<Item, { kind: "answer" }>; items: Item[] }) {
  return (
    <div class="answer-block">
      <Markdown text={item.text} />
      <div class="answer-actions">
        <CopyMenu answerText={item.text.trim()} turnText={() => turnToText(turnSlice(items, item.id))} />
      </div>
    </div>
  )
}

export function TranscriptItem({ item, uid, settled, items }: { item: Item; uid: string; settled: boolean; items: Item[] }) {
  switch (item.kind) {
    case "banner":
      return null
    case "user":
      return <div class="user">{item.text}</div>
    case "plan":
      return (
        <div class="plan">
          <div class="plan-head">{item.russian ? "Предложенный план" : "Proposed plan"}</div>
          <Markdown text={item.markdown} />
        </div>
      )
    case "step":
      if (HIDDEN_STEPS.has(item.action)) return null
      return (
        <div class="work quiet">
          <Node kind="idle" />
          <span class="verb">{verbOf(item.action)}</span>
          <span class="file"><FileIcon action={item.action} path={item.target} /><span class="file-name">{item.target}</span></span>
        </div>
      )
    case "tool":
      // The later calls of one model response sit under the first as one group.
      return item.batchIndex > 0 ? <div class="in-batch"><Tool item={item} /></div> : <Tool item={item} />
    case "thought":
      return <Thought item={item} uid={uid} settled={settled} />
    case "explored":
      return <Explored item={item} />
    case "say":
      return <Say text={item.text} settled={settled} />
    case "answer":
      return <Answer item={item} items={items} />
    case "note":
      return <div class={`note ${item.tone}`}>{item.text}</div>
    case "job":
      return <JobCard item={item} />
    case "job_notice":
      return <div class="note job-notice">{jobsCopy(item.russian).told}{"\n"}{item.text}</div>
  }
}
