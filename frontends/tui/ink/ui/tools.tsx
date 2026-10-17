import { Box, Text } from "ink"
import { useWindowSize } from "./window-size.js"
import { useContext, type ReactNode } from "react"

import { diffLines, numberWidth, pairRows, toLines, type DiffRow } from "../diff.js"
import { clipLines, fit, outputLines } from "../format.js"
import { actionTarget, type ActionResult, type Item } from "../state.js"
import { ViewContext } from "./context.js"
import { jobsCopy } from "./jobs.js"
import { color, planStepColor } from "./theme.js"

type ToolItem = Extract<Item, { kind: "tool" }>

const WRITE_LINES = 12
const DIFF_ROWS = 30
const OUTPUT_LINES = 15
export const SPLIT_MIN_COLUMNS = 100
// Body indent plus one spare column: a row painted to the very edge wraps in
// some terminals.
const INDENT = 2
const MARGIN = 3

export const str = (value: unknown): string => (typeof value === "string" ? value : "")

export function commandLine(args: Record<string, unknown>): string {
  const command = args.command
  return Array.isArray(command) ? command.map(String).join(" ") : str(command)
}

function Failure({ result }: { result: ActionResult }) {
  const detail = result.output.trim()
  return (
    <>
      <Text color={color.err} wrap="truncate-end">✗ {result.summary}</Text>
      {detail && !result.summary.includes(detail) ? <Output text={result.output} /> : null}
    </>
  )
}

function Output({ text }: { text: string }) {
  const { shown, more } = clipLines(outputLines(text), OUTPUT_LINES)
  return (
    <Box flexDirection="column">
      {shown.map((line, i) => (
        <Text key={i} color={color.dim} wrap="truncate-end">{line || " "}</Text>
      ))}
      {more > 0 ? <Text color={color.faint}>… {more} more lines</Text> : null}
    </Box>
  )
}

interface RowProps {
  no: number | null
  numW: number
  sign: string
  text: string
  width: number
  bg?: string
  signColor?: string
  textColor?: string
}

export function CodeRow({ no, numW, sign, text, width, bg, signColor, textColor }: RowProps) {
  const gutter = ` ${no === null ? "" : String(no)}`.padStart(numW + 1).padEnd(numW + 2)
  const mark = sign ? `${sign} ` : ""
  const body = fit(text, width - gutter.length - mark.length)
  return (
    <Text backgroundColor={bg} wrap="truncate-end">
      <Text color={color.faint}>{gutter}</Text>
      <Text color={signColor}>{mark}</Text>
      <Text color={textColor}>{body}</Text>
    </Text>
  )
}

function Header({ mark, markColor, label, path }: { mark: string; markColor?: string; label: string; path: string }) {
  return (
    <Text wrap="truncate-end">
      <Text color={markColor ?? color.dim}>{mark} </Text>
      <Text color={color.dim}>{label} </Text>
      <Text>{path}</Text>
    </Text>
  )
}

export function More({ count }: { count: number }) {
  return count > 0 ? <Text color={color.faint}>… {count} more lines</Text> : null
}

function Body({ children }: { children: ReactNode }) {
  return (
    <Box flexDirection="column" paddingLeft={INDENT}>
      {children}
    </Box>
  )
}

function WriteCard({ item, width }: { item: ToolItem; width: number }) {
  const { result } = item
  const path = result.path ?? str(item.args.path)
  const lines = toLines(str(item.args.content))
  const first = result.first_line ?? 1
  const shown = lines.slice(0, WRITE_LINES)
  const numW = String(first + shown.length).length
  return (
    <Box flexDirection="column" marginTop={1}>
      <Header mark="#" label="Wrote" path={path} />
      <Body>
        {result.ok ? (
          <>
            {shown.map((text, i) => (
              <CodeRow key={i} no={first + i} numW={numW} sign="" text={text} width={width} bg={color.panel} />
            ))}
            <More count={lines.length - shown.length} />
          </>
        ) : (
          <Failure result={result} />
        )}
      </Body>
    </Box>
  )
}

function AppendCard({ item, width }: { item: ToolItem; width: number }) {
  const { result } = item
  const path = result.path ?? str(item.args.path)
  const lines = toLines(str(item.args.content))
  const first = result.first_line ?? 1
  const shown = lines.slice(0, WRITE_LINES)
  const numW = String(first + shown.length).length
  return (
    <Box flexDirection="column" marginTop={1}>
      <Header mark="+" markColor={color.ok} label="Appended" path={path} />
      <Body>
        {result.ok ? (
          <>
            {shown.map((text, i) => (
              <CodeRow key={i} no={first + i} numW={numW} sign="+" signColor={color.ok} text={text} width={width} bg={color.addBg} />
            ))}
            <More count={lines.length - shown.length} />
          </>
        ) : (
          <Failure result={result} />
        )}
      </Body>
    </Box>
  )
}

function unifiedRow(row: DiffRow, numW: number, width: number, key: number) {
  if (row.kind === "ctx") return <CodeRow key={key} no={row.newNo} numW={numW} sign=" " text={row.text} width={width} textColor={color.dim} />
  if (row.kind === "del") return <CodeRow key={key} no={row.oldNo} numW={numW} sign="−" signColor={color.err} text={row.text} width={width} bg={color.delBg} />
  return <CodeRow key={key} no={row.newNo} numW={numW} sign="+" signColor={color.ok} text={row.text} width={width} bg={color.addBg} />
}

function splitCell(row: DiffRow | null, numW: number, width: number) {
  if (!row) return <Text>{" ".repeat(width)}</Text>
  if (row.kind === "ctx") return <CodeRow no={row.oldNo} numW={numW} sign=" " text={row.text} width={width} textColor={color.dim} />
  if (row.kind === "del") return <CodeRow no={row.oldNo} numW={numW} sign="−" signColor={color.err} text={row.text} width={width} bg={color.delBg} />
  return <CodeRow no={row.newNo} numW={numW} sign="+" signColor={color.ok} text={row.text} width={width} bg={color.addBg} />
}

export function DiffBody({ rows, width, split, limit = DIFF_ROWS }: { rows: DiffRow[]; width: number; split: boolean; limit?: number }) {
  const numW = numberWidth(rows)
  if (!split) {
    const shown = rows.slice(0, limit)
    return (
      <>
        {shown.map((row, i) => unifiedRow(row, numW, width, i))}
        <More count={rows.length - shown.length} />
      </>
    )
  }
  const paired = pairRows(rows)
  const shown = paired.slice(0, limit)
  const half = Math.floor((width - 1) / 2)
  return (
    <>
      {shown.map((pair, i) => (
        <Box key={i}>
          <Box width={half}>{splitCell(pair.left, numW, half)}</Box>
          <Text> </Text>
          <Box width={half}>{splitCell(pair.right, numW, half)}</Box>
        </Box>
      ))}
      <More count={paired.length - shown.length} />
    </>
  )
}

function EditCard({ item, width, columns }: { item: ToolItem; width: number; columns: number }) {
  const { result, args } = item
  const path = result.path ?? str(args.path)
  const rows = result.ok ? diffLines(str(args.old_text), str(args.new_text), result.first_line ?? 1) : []
  const count = typeof args.count === "number" ? args.count : 1
  return (
    <Box flexDirection="column" marginTop={1}>
      <Header mark="←" label="Edit" path={path} />
      <Body>
        {result.ok ? <DiffBody rows={rows} width={width} split={columns >= SPLIT_MIN_COLUMNS} /> : <Failure result={result} />}
        {result.ok && count > 1 ? <Text color={color.dim}>{result.summary}</Text> : null}
      </Body>
    </Box>
  )
}

function ShellCard({ item }: { item: ToolItem }) {
  const { result } = item
  const code = result.exit_code
  const exited = code !== null && code !== 0
  const { russian } = useContext(ViewContext)
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        <Text color={color.dim}>$ </Text>
        <Text>{commandLine(item.args)}</Text>
        {item.args.background === true ? <Text color={color.dim}> ({jobsCopy(russian === true).background})</Text> : null}
      </Text>
      <Body>
        {result.output.trim() ? <Output text={result.output} /> : <Text color={color.faint}>(no output)</Text>}
        {exited ? <Text color={color.err}>Command exited with code {code}</Text> : null}
        {!exited && !result.ok ? <Text color={color.err} wrap="truncate-end">✗ {result.summary}</Text> : null}
      </Body>
    </Box>
  )
}

export function diffLineColor(line: string): string | undefined {
  if (/^(diff --git|index |--- |\+\+\+ |@@)/.test(line)) return color.dim
  if (line.startsWith("+")) return color.ok
  if (line.startsWith("-")) return color.err
  return undefined
}

function GitDiffCard({ item }: { item: ToolItem }) {
  const { result } = item
  const { shown, more } = clipLines(outputLines(result.output), OUTPUT_LINES)
  const target = actionTarget(item.args)
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        <Text color={color.dim}>$ </Text>
        <Text>git diff{target ? ` ${target}` : ""}</Text>
      </Text>
      <Body>
        {!result.ok ? <Text color={color.err} wrap="truncate-end">✗ {result.summary}</Text> : null}
        {result.ok && shown.length === 0 ? <Text color={color.faint}>(no changes)</Text> : null}
        {shown.map((line, i) => (
          <Text key={i} color={diffLineColor(line)} wrap="truncate-end">{line || " "}</Text>
        ))}
        {more > 0 ? <Text color={color.faint}>… {more} more lines</Text> : null}
      </Body>
    </Box>
  )
}

const LINE_ACTIONS = new Set(["web_search", "fetch_url"])

function LineCard({ item }: { item: ToolItem }) {
  const { result } = item
  const dot = result.ok ? (planStepColor[item.planStep] ?? color.dim) : color.err
  const detail = LINE_ACTIONS.has(item.action) ? result.summary : actionTarget(item.args)
  return (
    <Box flexDirection="column">
      <Text wrap="truncate-end">
        <Text color={dot}>● </Text>
        <Text bold>{item.action}</Text>
        {detail ? <Text color={result.ok ? color.dim : color.err}>  {detail}</Text> : null}
      </Text>
      {!result.ok && !LINE_ACTIONS.has(item.action) ? (
        <Body>
          <Failure result={result} />
        </Body>
      ) : null}
      {result.ok && item.action === "delegate_task" && result.output.trim() ? (
        <Body>
          <Output text={result.output} />
        </Body>
      ) : null}
    </Box>
  )
}

export function ToolCard({ item }: { item: ToolItem }) {
  const { columns } = useWindowSize()
  const width = Math.max(20, columns - INDENT - MARGIN)
  switch (item.action) {
    case "write_file":
      return <WriteCard item={item} width={width} />
    case "append_file":
      return <AppendCard item={item} width={width} />
    case "replace_in_file":
      return <EditCard item={item} width={width} columns={columns} />
    case "run_shell":
      return <ShellCard item={item} />
    case "git_diff":
      return <GitDiffCard item={item} />
    default:
      return <LineCard item={item} />
  }
}
