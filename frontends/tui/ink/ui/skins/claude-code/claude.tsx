// The claude skin: the same items and live state as the ink skin, drawn the way
// Claude Code draws a session (● calls, ⎿ results, ✻ spinner, ruled input).
import { Box, Text, useBoxMetrics, useInput, type DOMElement } from "ink"
import { useWindowSize } from "../../window-size.js"
import { memo, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react"

import { diffLines, toLines } from "../../../diff.js"
import { clip, clipLines, contextLabel, formatSeconds, outputLines } from "../../../format.js"
import { actionTarget, requestIsRussian, type ActionResult, type ExploreCounts, type Item, type State } from "../../../state.js"
import { ViewContext } from "../../context.js"
import { MASCOT_COLUMNS, MASCOT_ROWS, Mascot, syncMascotImage } from "./mascot.js"
import { jobFailed, jobLabel, jobsCopy, jobTail } from "../../jobs.js"
import { Markdown, renderInline } from "../../markdown.js"
import { color } from "../../theme.js"
import { coolDown, shimmerColor } from "./shimmer.js"
import { tipFor } from "./tips.js"
import { doneLine, span, spinnerWord } from "./verbs.js"
import { ClaudeDiff, ClaudePreview, diffSummary } from "./claude-diff.js"
import { More, commandLine, diffLineColor, str } from "../../tools.js"

type ToolItem = Extract<Item, { kind: "tool" }>

const OUTPUT_LINES = 10
// The lines of a finished job shown folded, and with Ctrl+O.
const JOB_LINES = 3
const JOB_LINES_EXPANDED = 12
// "⎿" and its padding: the body of a result starts this many cells in.
const RESULT_INDENT = 5
const MARGIN = 3
// How long "thought for Ns" stays in the spinner after the thinking ends.
const THOUGHT_SHOWN_MS = 2000
const CYRILLIC = /\p{Script=Cyrillic}/u
const FILE_ACTIONS = new Set(["write_file", "append_file", "replace_in_file"])
const TERMINAL_ACTIONS = new Set(["final_answer", "report_to_user"])
const SPINNER = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"]

const TOOL_NAMES: Record<string, string> = {
  read_file: "Read",
  read_multiple_files: "Read",
  write_file: "Write",
  append_file: "Update",
  replace_in_file: "Update",
  list_dir: "List",
  list_tree: "List",
  find_files: "Search",
  search_code: "Search",
  git_status: "Bash",
  git_diff: "Bash",
  run_shell: "Bash",
  web_search: "Web Search",
  fetch_url: "Fetch",
  delegate_task: "Task",
  job_output: "Job output",
  job_stop: "Stop job",
}

function toolHeader(action: string, args: Record<string, unknown>, russian = false): { name: string; target: string } {
  const name = TOOL_NAMES[action] ?? action
  if (action === "run_shell") {
    const background = args.background === true ? ` (${jobsCopy(russian).background})` : ""
    return { name: `${name}${background}`, target: commandLine(args) }
  }
  if (action === "git_status") return { name, target: "git status" }
  if (action === "git_diff") return { name, target: `git diff ${actionTarget(args)}`.trim() }
  return { name, target: actionTarget(args) }
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

// Russian noun form for a count: 1 файл, 2 файла, 5 файлов.
function pluralRu(count: number, one: string, few: string, many: string): string {
  const tail = count % 100
  if (tail >= 11 && tail <= 14) return `${count} ${many}`
  const last = count % 10
  return `${count} ${last === 1 ? one : last >= 2 && last <= 4 ? few : many}`
}

// "Searched for 2 patterns, read 3 files" — the order Claude Code lists them in.
export function exploreLine(counts: ExploreCounts, shells = 0, russian = false): string {
  const parts = russian
    ? [
        counts.searches > 0 ? `искал ${pluralRu(counts.searches, "запрос", "запроса", "запросов")}` : "",
        counts.reads > 0 ? `прочитал ${pluralRu(counts.reads, "файл", "файла", "файлов")}` : "",
        counts.lists > 0 ? `просмотрел ${pluralRu(counts.lists, "каталог", "каталога", "каталогов")}` : "",
        counts.git > 0 ? `выполнил ${pluralRu(counts.git, "проверку", "проверки", "проверок")} git` : "",
        shells > 0 ? `выполнил ${pluralRu(shells, "команду", "команды", "команд")} shell` : "",
      ]
    : [
        counts.searches > 0 ? `searched for ${plural(counts.searches, "pattern", "patterns")}` : "",
        counts.reads > 0 ? `read ${plural(counts.reads, "file", "files")}` : "",
        counts.lists > 0 ? `listed ${plural(counts.lists, "directory", "directories")}` : "",
        counts.git > 0 ? `ran ${plural(counts.git, "git check", "git checks")}` : "",
        shells > 0 ? `ran ${plural(shells, "shell command", "shell commands")}` : "",
      ]
  const text = parts.filter(Boolean).join(", ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function Banner() {
  const { info, effort, workspace } = useContext(ViewContext)
  const reasoning = effort && effort !== "false" ? ` with ${effort} effort` : ""
  const detail = [`${info.model}${reasoning}`.trim(), info.provider].filter(Boolean).join(" · ")
  return (
    <Box marginTop={1} marginBottom={1}>
      <Box flexShrink={0} marginRight={2}>
        <Mascot />
      </Box>
      <Rest taken={MASCOT_COLUMNS + 2}>
        {/* The three text rows sit in the middle of the squirrel. */}
        <Box height={Math.max(0, Math.floor((MASCOT_ROWS - 3) / 2))} />
        <Text wrap="truncate-end">
          <Text bold>Kvist</Text>
          {info.kvistVersion ? <Text color={color.dim}> v{info.kvistVersion}</Text> : null}
        </Text>
        <Text color={color.dim} wrap="truncate-end">{detail}</Text>
        <Text color={color.dim} wrap="truncate-end">{workspace}</Text>
      </Rest>
    </Box>
  )
}

// The rest of a row beside `taken` cells of glyphs and indent. Ink does not
// shrink a wrapping Text to the room a sibling leaves (it lays the row out two
// cells too wide and the terminal wraps it mid-word), so the room is stated.
function Rest({ taken, children }: { taken: number; children: ReactNode }) {
  const { columns } = useWindowSize()
  return (
    <Box flexDirection="column" width={Math.max(10, columns - taken)}>
      {children}
    </Box>
  )
}

// The glyph in front of a wrapping block; it keeps its width when the row is tight.
function Mark({ tone, children }: { tone?: string; children: string }) {
  return (
    <Box flexShrink={0}>
      <Text color={tone}>{children}</Text>
    </Box>
  )
}

function Result({ children }: { children: ReactNode }) {
  return (
    <Box paddingLeft={2}>
      <Mark tone={color.dim}>⎿  </Mark>
      <Rest taken={RESULT_INDENT}>{children}</Rest>
    </Box>
  )
}

function Call({ tone, name, target, children }: { tone: string; name: string; target: string; children?: ReactNode }) {
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        <Text color={tone}>● </Text>
        <Text bold>{name}</Text>
        {target ? <Text>({clip(target, 100)})</Text> : null}
      </Text>
      {children}
    </Box>
  )
}

function Lines({ text, tone }: { text: string; tone?: string }) {
  const { shown, more } = clipLines(outputLines(text), OUTPUT_LINES)
  return (
    <>
      {shown.map((line, i) => (
        <Text key={i} color={tone} wrap="truncate-end">{line || " "}</Text>
      ))}
      {more > 0 ? <Text color={color.dim}>… +{plural(more, "line", "lines")}</Text> : null}
    </>
  )
}

function Failure({ result, lead }: { result: ActionResult; lead?: string }) {
  const detail = result.output.trim()
  return (
    <Result>
      <Text color={color.err} wrap="truncate-end">{lead ?? result.summary}</Text>
      {detail && !(lead ?? result.summary).includes(detail) ? <Lines text={result.output} tone={color.dim} /> : null}
    </Result>
  )
}

function Preview({ item, width }: { item: ToolItem; width: number }) {
  const { result, args } = item
  const path = result.path ?? str(args.path)
  const content = str(args.content)
  const appended = item.action === "append_file"
  return (
    <Result>
      <Text>{appended ? "Appended" : "Wrote"} {plural(toLines(content).length, "line", "lines")} to <Text bold>{path}</Text></Text>
      <ClaudePreview path={path} content={content} first={result.first_line ?? 1} appended={appended} width={width} />
    </Result>
  )
}

function Edit({ item, width }: { item: ToolItem; width: number }) {
  const { result, args } = item
  const path = result.path ?? str(args.path)
  const first = result.first_line ?? 1
  const rows = diffLines(str(args.old_text), str(args.new_text), first)
  const added = rows.filter((row) => row.kind === "add").length
  const removed = rows.filter((row) => row.kind === "del").length
  return (
    <Result>
      <Text wrap="truncate-end">{diffSummary(added, removed)}</Text>
      <ClaudeDiff path={path} oldText={str(args.old_text)} newText={str(args.new_text)} first={first} width={width} />
    </Result>
  )
}

function ToolBody({ item, width }: { item: ToolItem; width: number }) {
  const { result } = item
  if (item.action === "run_shell") {
    const code = result.exit_code
    if (code !== null && code !== 0) return <Failure result={result} lead={`Error: Exit code ${code}`} />
    if (!result.ok) return <Failure result={result} />
    return <Result>{result.output.trim() ? <Lines text={result.output} /> : <Text color={color.dim}>(No content)</Text>}</Result>
  }
  if (!result.ok) return <Failure result={result} />
  switch (item.action) {
    case "write_file":
    case "append_file":
      return <Preview item={item} width={width} />
    case "replace_in_file":
      return <Edit item={item} width={width} />
    case "git_diff": {
      const { shown, more } = clipLines(outputLines(result.output), OUTPUT_LINES)
      return (
        <Result>
          {shown.length === 0 ? <Text color={color.dim}>(No changes)</Text> : null}
          {shown.map((line, i) => <Text key={i} color={diffLineColor(line)} wrap="truncate-end">{line || " "}</Text>)}
          {more > 0 ? <Text color={color.dim}>… +{plural(more, "line", "lines")}</Text> : null}
        </Result>
      )
    }
    case "delegate_task":
      return <Result>{result.output.trim() ? <Lines text={result.output} tone={color.dim} /> : <Text color={color.dim}>{result.summary}</Text>}</Result>
    default:
      return result.summary ? <Result><Text color={color.dim} wrap="truncate-end">{result.summary}</Text></Result> : null
  }
}

function Tool({ item }: { item: ToolItem }) {
  const { columns } = useWindowSize()
  const { russian } = useContext(ViewContext)
  const { name, target } = toolHeader(item.action, item.args, russian === true)
  return (
    <Call tone={item.result.ok ? (FILE_ACTIONS.has(item.action) ? color.file : color.ok) : color.err} name={name} target={target}>
      <ToolBody item={item} width={Math.max(20, columns - RESULT_INDENT - MARGIN)} />
    </Call>
  )
}

function FoldedRun({ counts, shells }: { counts: ExploreCounts; shells: number }) {
  const { russian } = useContext(ViewContext)
  return (
    <Box marginTop={1} paddingLeft={2}>
      <Text color={color.dim}>{exploreLine(counts, shells, russian === true)}</Text>
    </Box>
  )
}

function Thought({ item }: { item: Extract<Item, { kind: "thought" }> }) {
  const { russian } = useContext(ViewContext)
  const label = item.seconds > 0 ? (russian ? `Думал ${span(item.seconds, true)}` : `Thought for ${formatSeconds(item.seconds)}`) : russian ? "Думал" : "Thought"
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={color.thought} italic>✻ {label}</Text>
      {item.text ? <Box paddingLeft={2}><Text color={color.thought} italic>{renderInline(item.text)}</Text></Box> : null}
    </Box>
  )
}

function ExploredLine({ counts, shells = 0 }: { counts: ExploreCounts; shells?: number }) {
  const { russian } = useContext(ViewContext)
  return <Call tone={color.ok} name={exploreLine(counts, shells, russian === true)} target="" />
}

function Note({ item }: { item: Extract<Item, { kind: "note" }> }) {
  const tone = item.tone === "info" ? color.dim : item.tone === "warn" ? color.warn : item.tone === "error" ? color.err : color.faint
  return (
    <Result>
      <Text color={tone}>{item.tone === "debug" ? "› " : ""}{item.text}</Text>
    </Result>
  )
}

// A background job ended: a call line with the way it ended, and its last lines under it.
function JobEnded({ item }: { item: Extract<Item, { kind: "job" }> }) {
  const { expanded } = useContext(ViewContext)
  const copy = jobsCopy(item.russian)
  const failed = jobFailed(item.status, item.exitCode)
  const { shown, earlier } = jobTail(item.output, expanded ? JOB_LINES_EXPANDED : JOB_LINES)
  const took = item.seconds >= 1 ? ` · ${formatSeconds(item.seconds)}` : ""
  return (
    <Call tone={failed ? color.err : color.ok} name={jobLabel(item)} target="">
      <Result>
        <Text color={failed ? color.err : color.dim} wrap="truncate-end">{copy.ended(item.status, item.exitCode)}{took}</Text>
        {earlier > 0 ? <Text color={color.dim}>{copy.earlier(earlier)}</Text> : null}
        {shown.length === 0 ? <Text color={color.dim}>{copy.noOutput}</Text> : null}
        {shown.map((line, i) => <Text key={i} color={color.dim} wrap="truncate-end">{line || " "}</Text>)}
      </Result>
    </Call>
  )
}

// A preview only: the whole note is in the session journal; Ctrl+O shows more of it.
function JobNotice({ item }: { item: Extract<Item, { kind: "job_notice" }> }) {
  const { expanded } = useContext(ViewContext)
  const lines = item.text.split("\n").filter((line) => line.trim())
  const shown = expanded ? lines.slice(0, JOB_LINES_EXPANDED) : [`${clip(lines[0] ?? "", 100)}${lines.length > 1 ? " …" : ""}`]
  return (
    <Result>
      {shown.map((line, i) => (
        <Text key={i} color={color.faint} wrap="truncate-end">{i === 0 ? `${jobsCopy(item.russian).told} ` : ""}{line}</Text>
      ))}
    </Result>
  )
}

function Message({ text }: { text: string }) {
  return (
    <Box marginTop={1}>
      <Mark>● </Mark>
      <Rest taken={2}><Markdown text={text} /></Rest>
    </Box>
  )
}

export function ClaudeItemView({ item, showDebug }: { item: Item; showDebug: boolean }) {
  switch (item.kind) {
    case "banner":
      return <Banner />
    case "user":
      return (
        <Box marginTop={1} backgroundColor={color.panel}>
          <Mark tone={color.dim}>❯ </Mark>
          <Rest taken={2}><Text>{item.text}</Text></Rest>
        </Box>
      )
    case "plan":
      return (
        <Call tone={color.text} name={item.russian ? "Предложенный план" : "Proposed plan"} target="">
          <Result>
            <Markdown text={item.markdown} />
          </Result>
        </Call>
      )
    case "step": {
      // The answer itself follows; a "final_answer" call line would only repeat it.
      if (TERMINAL_ACTIONS.has(item.action)) return null
      const { name, target } = toolHeader(item.action, { path: item.target })
      return <Call tone={color.dim} name={name} target={target} />
    }
    case "tool":
      return <Tool item={item} />
    case "thought":
      return (
        <Thought item={item} />
      )
    case "explored":
      return <ExploredLine counts={item.counts} />
    case "say":
    case "answer":
      return item.fresh ? <FreshMessage text={item.text} /> : <Message text={item.text} />
    case "done":
      return (
        <Box marginTop={1}>
          <Text color={color.dim}>✻ {doneLine(item)}</Text>
        </Box>
      )
    case "job":
      return <JobEnded item={item} />
    case "job_notice":
      return <JobNotice item={item} />
    case "note":
      if (item.tone === "debug" && !showDebug) return null
      return <Note item={item} />
  }
}

function useNow(active: boolean, every: number): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), every)
    return () => clearInterval(timer)
  }, [active, every])
  return now
}

function Spinner({ glyph, text, detail, tip = "", waiting = 0, now = 0 }: { glyph: string; text: string; detail: string; tip?: string; waiting?: number; now?: number }) {
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        {[...`${glyph} ${text}…`].map((char, index) => (
          <Text key={index} color={shimmerColor(waiting, now, index)}>{char}</Text>
        ))}
        <Text> </Text>
        <Text color={color.dim}>({detail})</Text>
      </Text>
      {tip ? (
        <Result>
          <Text color={color.dim} wrap="truncate-end">{tip}</Text>
        </Result>
      ) : null}
    </Box>
  )
}

// "8.5k" for 8 512: the count Claude Code shows next to the spinner.
function tokenCount(count: number): string {
  return count < 1000 ? String(count) : `${(count / 1000).toFixed(1)}k`
}

// The end of the transcript that is still changing: the read group, the call
// waiting for its result and the answer as it streams.
// Reveals a growing text at a steady pace. The core sends what it has about nine times a second
// and a fast model is done in half a second, so shown as it arrives the text lands in a few blocks.
const REVEAL_MS = 33
const CATCH_UP = 5
// A long report would otherwise open with a fifth of itself in one tick.
const MAX_STEP = 24

// What the live answer had shown when it was committed: the settled answer continues from there
// instead of retyping from the first character.
let lastLiveShown = ""

function useTypewriter(text: string, start = 0): string {
  const [revealed, setRevealed] = useState(Math.min(start, text.length))
  const goal = useRef(text.length)
  goal.current = text.length
  useEffect(() => {
    const timer = setInterval(() => {
      // Each tick closes a fifth of the gap, so a burst is spread over about half a second.
      setRevealed((n) =>
        n > goal.current ? goal.current : n + Math.min(MAX_STEP, Math.ceil((goal.current - n) / CATCH_UP)),
      )
    }, REVEAL_MS)
    return () => clearInterval(timer)
  }, [])
  return text.slice(0, revealed)
}

function LiveThought({ text }: { text: string }) {
  return (
    <Box marginTop={1} paddingLeft={2}>
      <Text color={color.thought} italic>{renderInline(useTypewriter(text))}</Text>
    </Box>
  )
}

function LiveAnswer({ text }: { text: string }) {
  const shown = useTypewriter(text)
  lastLiveShown = shown
  return <Message text={shown} />
}

// An answer that arrived during the turn: revealed gradually, from where the live stream stopped.
function FreshMessage({ text }: { text: string }) {
  const [start] = useState(() => {
    const from = text.startsWith(lastLiveShown) ? lastLiveShown.length : 0
    lastLiveShown = ""
    return from
  })
  return <Message text={useTypewriter(text, start)} />
}

const COMMAND_LINES = 4

// A shell command that is still running: what it is and how long it has been going.
function RunningShell({ call }: { call: State["inflight"][number] }) {
  const { russian } = useContext(ViewContext)
  const now = useNow(true, 1000)
  const lines = commandLine(call.args).split("\n")
  const shown = lines.slice(0, COMMAND_LINES)
  return (
    <Call tone={color.dim} name={`${russian ? "Выполняю команду shell" : "Running shell command"} · ${formatSeconds((now - call.at) / 1000)}…`} target="">
      <Result>
        {shown.map((line, index) => (
          <Text key={index} color={color.dim} wrap="truncate-end">{index === 0 ? "$ " : "  "}{line || " "}</Text>
        ))}
        {lines.length > shown.length ? <Text color={color.dim}>… +{plural(lines.length - shown.length, "line", "lines")}</Text> : null}
      </Result>
    </Call>
  )
}

export function ClaudeLiveBody({ state }: { state: State }) {
  // The answer itself follows a final_answer call, so the call gets no line of its own.
  const calls = state.inflight.filter((call) => !TERMINAL_ACTIONS.has(call.action))
  const inflight = calls.at(-1) ?? null
  const header = inflight ? toolHeader(inflight.action, inflight.args, requestIsRussian(state)) : null
  const live = state.live
  return (
    <>
      {state.running && live?.thinking && live.reasoning ? <LiveThought text={live.reasoning} /> : null}
      {inflight?.action === "run_shell" ? <RunningShell call={inflight} /> : null}
      {inflight && header && inflight.action !== "run_shell" ? (
        <Call tone={color.dim} name={header.name} target={header.target}>
          {state.pending ? null : <Result><Text color={color.dim}>{requestIsRussian(state) ? "Выполняется…" : "Running…"}</Text></Result>}
        </Call>
      ) : null}
      {state.running && !state.pending && state.live?.answer ? <LiveAnswer text={state.live.answer} /> : null}
    </>
  )
}

// The status line pinned above the input while a turn or a command runs.
export function ClaudeSpinner({ state }: { state: State }) {
  const now = useNow(state.running || state.command !== null, 120)
  const glyph = SPINNER[Math.floor(now / 120) % SPINNER.length] ?? "·"
  // A running call has its own clock; only the wait for the model warms the colour, and the
  // colour cools down gradually once the model has answered.
  const target = state.running && state.waitingSince && state.inflight.length === 0 ? (now - state.waitingSince) / 1000 : 0
  const shown = useRef({ waiting: 0, at: now })
  shown.current = { waiting: coolDown(shown.current.waiting, target, (now - shown.current.at) / 1000), at: now }
  const russian = requestIsRussian(state)
  if (state.command && !state.pending) return <Spinner glyph={glyph} text={`${russian ? "Выполняю" : "Running"} /${state.command}`} detail={russian ? "esc — отмена" : "esc to cancel"} waiting={shown.current.waiting} now={now} />
  if (!state.running || state.pending) return null

  const turnSeconds = state.startedAt ? (now - state.startedAt) / 1000 : 0
  const request = state.items.findLast((item) => item.kind === "user")
  const role = state.info.routedRole ?? state.info.role
  const thinking = state.live?.thinking ?? false
  const reasoning = state.effort && state.effort !== "false" ? ` with ${state.effort} effort` : ""
  const justThought = state.thought && now - state.thought.at < THOUGHT_SHOWN_MS ? `thought for ${formatSeconds(state.thought.seconds)}` : ""
  // The verb is decoration; what the core reports it is doing stays in the parentheses.
  const doing = thinking ? `thinking${reasoning}` : justThought || clip(state.status?.text ?? "", 48)
  // Output tokens of this turn, as far as the core has reported them (it does so after each model call).
  const tokens = Math.max(0, state.usage.output - state.turnStartOutput)
  const detail = [formatSeconds(turnSeconds), tokens > 0 ? `↓ ${tokenCount(tokens)} tokens` : "", doing].filter(Boolean).join(" · ")
  const text = request?.kind === "user" ? (request.request ?? request.text) : ""
  const tip = tipFor(state.startedAt ?? 0, text, turnSeconds)
  return (
    <Spinner
      glyph={glyph}
      text={spinnerWord(role, state.startedAt ?? 0, turnSeconds, text)}
      detail={detail}
      waiting={shown.current.waiting}
      now={now}
      tip={tip ? `${CYRILLIC.test(text) ? "Совет" : "Tip"}: ${tip}` : ""}
    />
  )
}

const MemoItem = memo(ClaudeItemView)

type Entry = { key: number; item: Item } | { key: number; counts: ExploreCounts; shells: number }

// A shell command that succeeded, or a run of reads: what the person needs to know is that they
// happened, and Ctrl+O shows the detail. A failed command is never folded away.
function quiet(item: Item): boolean {
  if (item.kind === "explored") return true
  return item.kind === "tool" && item.action === "run_shell" && item.result.ok && (item.result.exit_code ?? 0) === 0
}

// A thought with no text of its own is only a duration; between two folded items it adds no
// information, so it folds with them instead of splitting the run into one line per call.
function bareThought(item: Item): boolean {
  return item.kind === "thought" && !item.text
}

// Where the run of quiet items that starts at `start` ends (exclusive), skipping bare thoughts
// that sit between quiet items.
function quietRunEnd(items: readonly Item[], start: number): number {
  let end = start
  let scan = start
  while (scan < items.length) {
    const item = items[scan]!
    if (quiet(item)) end = ++scan
    else if (bareThought(item)) scan++
    else break
  }
  return end
}

// Consecutive quiet items become one dim line, "Searched for 2 patterns, ran 2 shell commands".
// A lone read summary keeps its own line.
export function collapse(items: readonly Item[]): Entry[] {
  const entries: Entry[] = []
  for (let start = 0; start < items.length; ) {
    const first = items[start]!
    if (!quiet(first)) {
      entries.push({ key: first.id, item: first })
      start++
      continue
    }
    const end = quietRunEnd(items, start)
    const run = items.slice(start, end).filter(quiet)
    start = end
    const shells = run.filter((item) => item.kind === "tool").length
    if (run.length === 1 && shells === 0) {
      entries.push({ key: run[0]!.id, item: run[0]! })
      continue
    }
    const counts = { reads: 0, searches: 0, lists: 0, git: 0 }
    for (const item of run) {
      if (item.kind !== "explored") continue
      counts.reads += item.counts.reads
      counts.searches += item.counts.searches
      counts.lists += item.counts.lists
      counts.git += item.counts.git
    }
    entries.push({ key: run[0]!.id, counts, shells })
  }
  return entries
}

// Claude Code's fullscreen layout: the transcript fills the window from the top
// (the welcome block first, the live thought and answer last), and the spinner,
// suggestions, input and footer stay pinned to the bottom. The terminal's own scrollback does not exist on the
// alternate screen, so the transcript scrolls here: PageUp/PageDown move it,
// and a new request or reply keeps the bottom in view unless it was scrolled.
export function ClaudeScreen({ items, showDebug, followKey, live, spinner, hint = null, children }: { items: readonly Item[]; showDebug: boolean; followKey: number; live: ReactNode; spinner: ReactNode; hint?: ReactNode; children: ReactNode }) {
  const { rows } = useWindowSize()
  const viewport = useRef<DOMElement>(null)
  const content = useRef<DOMElement>(null)
  const view = useBoxMetrics(viewport)
  const body = useBoxMetrics(content)
  const [scroll, setScroll] = useState(0)
  const previous = useRef(body.height)
  const max = Math.max(0, body.height - view.height)
  const shown = Math.min(scroll, max)
  const page = Math.max(1, Math.floor(view.height / 2))
  const { pointer, expanded } = useContext(ViewContext)
  // Whatever moved the text may have moved the mascot, and a picture does not move by itself.
  useEffect(() => syncMascotImage(pointer))
  const entries = useMemo(() => (expanded ? items.map((item): Entry => ({ key: item.id, item })) : collapse(items)), [items, expanded])
  // The wheel sends "toward newer" as a positive number of lines.
  const scrollBy = useRef((_lines: number) => {})
  scrollBy.current = (lines) => setScroll(Math.max(0, Math.min(max, shown - lines)))
  useEffect(() => pointer?.onWheel((lines) => scrollBy.current(lines)), [pointer])

  useEffect(() => {
    // Content that grows under a scrolled view must not move what is being read.
    if (scroll > 0 && body.height > previous.current) setScroll(scroll + body.height - previous.current)
    previous.current = body.height
  }, [body.height, scroll])
  useEffect(() => setScroll(0), [followKey])
  useInput((_, key) => {
    if (key.pageUp) setScroll(Math.min(max, shown + page))
    if (key.pageDown) setScroll(Math.max(0, shown - page))
    if (key.ctrl && key.home) setScroll(max)
    if (key.ctrl && key.end) setScroll(0)
  })

  return (
    <Box flexDirection="column" height={rows}>
      {/* Stacked from the bottom: a short transcript sits at the top above the spacer, a tall one
          ends at the bottom edge and overflows upward. Following the newest row therefore needs no
          measurement, which is what made the view jump by a frame when it did. */}
      <Box ref={viewport} flexGrow={1} flexDirection="column-reverse" overflow="hidden">
        <Box flexGrow={1} />
        <Box ref={content} flexDirection="column" flexShrink={0} marginBottom={-shown}>
          {entries.map((entry) => (
            <Box key={entry.key} flexDirection="column">
              {"item" in entry ? (
                <MemoItem item={entry.item} showDebug={showDebug} />
              ) : (
                <FoldedRun counts={entry.counts} shells={entry.shells} />
              )}
            </Box>
          ))}
          {live}
        </Box>
      </Box>
      <Box flexDirection="column" flexShrink={0}>
        {shown > 0 ? <Text color={color.dim}>  ↓ {shown} rows below · ctrl+end to follow</Text> : null}
        {spinner}
        {hint}
        {children}
      </Box>
    </Box>
  )
}

// Under the input: what the keys do now on the left, what is running on the right.
export function ClaudeFooter({ running, armed, usage, model, role, toast = "", exitWarning = null }: { running: boolean; armed: boolean; usage: State["usage"]; model: string; role: string; toast?: string; exitWarning?: "C" | "D" | null }) {
  const context = contextLabel(usage.context, usage.limit)
  const mode = [model, role].filter(Boolean).join(" · ")
  const { russian } = useContext(ViewContext)
  const keys = russian
    ? { armed: "esc ещё раз — пауза · ctrl+c останавливает всё", running: "esc — пауза · ctrl+c — остановить всё", idle: "/ — команды", exit: `Нажми Ctrl+${exitWarning} ещё раз, чтобы выйти и остановить агента` }
    : { armed: "esc again to pause · ctrl+c stops all", running: "esc to pause · ctrl+c to stop all", idle: "/ for commands", exit: `Press Ctrl+${exitWarning} again to exit and stop the agent` }
  return (
    <Box justifyContent="space-between" paddingX={2}>
      {armed ? <Text color={color.warn} wrap="truncate-end">{keys.armed}</Text> : toast ? <Text color={color.ok} wrap="truncate-end">{toast}</Text> : <Text color={color.dim} wrap="truncate-end">{running ? keys.running : keys.idle}</Text>}
      {exitWarning ? (
        <Text color={color.warn} wrap="truncate-end">{keys.exit}</Text>
      ) : (
        <Text color={color.dim} wrap="truncate-end">{[context, mode].filter(Boolean).join("  ")}</Text>
      )}
    </Box>
  )
}

// Under the pinned spinner, above the input: the reasoning level and the command that changes it.
export function ClaudeEffort({ effort }: { effort: string }) {
  if (!effort) return null
  return (
    <Box justifyContent="flex-end" paddingRight={1}>
      <Text color={color.dim}>● {effort === "false" ? "no reasoning" : effort} · /effort</Text>
    </Box>
  )
}
