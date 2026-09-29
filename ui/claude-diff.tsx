// The diff and file-preview rows of the claude skin: numbered, syntax-highlighted, with a
// red or green background across the whole row for a removed or added line.
import { Text } from "ink"
import { useMemo } from "react"

import { diffLines, numberWidth, toLines } from "../diff.js"
import { fitSegments, highlightLines, languageOf, type Segment } from "../highlight.js"
import { syntaxColor } from "./syntax.js"
import { color } from "./theme.js"
import { More } from "./tools.js"

const DIFF_ROWS = 30
const PREVIEW_ROWS = 12

export function diffSummary(added: number, removed: number): string {
  const lines = (count: number) => `${count} line${count === 1 ? "" : "s"}`
  if (added && removed) return `Added ${lines(added)}, removed ${lines(removed)}`
  if (added) return `Added ${lines(added)}`
  if (removed) return `Removed ${lines(removed)}`
  return "No changes"
}

function Code({ segments }: { segments: readonly Segment[] }) {
  return (
    <>
      {segments.map((segment, index) => (
        <Text key={index} color={syntaxColor(segment.scope)}>{segment.text}</Text>
      ))}
    </>
  )
}

interface RowProps {
  no: number | null
  numW: number
  // "+" or "−" for a changed row; a context row has none.
  sign: string
  segments: readonly Segment[]
  width: number
  bg?: string
  // The colour of the number and the sign.
  fg?: string
}

function Row({ no, numW, sign, segments, width, bg, fg }: RowProps) {
  const gutter = ` ${no === null ? "" : String(no)}`.padStart(numW + 1).padEnd(numW + 2)
  const mark = sign ? `${sign} ` : "  "
  return (
    <Text backgroundColor={bg} wrap="truncate-end">
      <Text color={fg ?? color.faint}>{gutter}{mark}</Text>
      <Code segments={fitSegments(segments, width - gutter.length - mark.length)} />
    </Text>
  )
}

export function ClaudeDiff({ path, oldText, newText, first, width }: { path: string; oldText: string; newText: string; first: number; width: number }) {
  const language = languageOf(path)
  const rows = useMemo(() => diffLines(oldText, newText, first), [oldText, newText, first])
  const before = useMemo(() => highlightLines(oldText, language), [oldText, language])
  const after = useMemo(() => highlightLines(newText, language), [newText, language])
  const shown = rows.slice(0, DIFF_ROWS)
  const numW = numberWidth(rows)
  return (
    <>
      {shown.map((row, index) => {
        const at = row.kind === "del" ? before[(row.oldNo ?? first) - first] : after[(row.newNo ?? first) - first]
        const segments = at ?? [{ text: row.text, scope: null }]
        if (row.kind === "del") return <Row key={index} no={row.oldNo} numW={numW} sign="−" segments={segments} width={width} bg={color.delBg} fg={color.delFg} />
        if (row.kind === "add") return <Row key={index} no={row.newNo} numW={numW} sign="+" segments={segments} width={width} bg={color.addBg} fg={color.addFg} />
        return <Row key={index} no={row.newNo} numW={numW} sign="" segments={segments} width={width} />
      })}
      <More count={rows.length - shown.length} />
    </>
  )
}

// The first lines of a file that was written; an append is drawn as added rows.
export function ClaudePreview({ path, content, first, appended, width }: { path: string; content: string; first: number; appended: boolean; width: number }) {
  const language = languageOf(path)
  const lines = useMemo(() => toLines(content), [content])
  const highlighted = useMemo(() => highlightLines(content, language), [content, language])
  const shown = lines.slice(0, PREVIEW_ROWS)
  const numW = String(first + shown.length).length
  return (
    <>
      {shown.map((text, index) => (
        <Row
          key={index}
          no={first + index}
          numW={numW}
          sign={appended ? "+" : ""}
          segments={highlighted[index] ?? [{ text, scope: null }]}
          width={width}
          bg={appended ? color.addBg : undefined}
          fg={appended ? color.addFg : undefined}
        />
      ))}
      <More count={lines.length - shown.length} />
    </>
  )
}
