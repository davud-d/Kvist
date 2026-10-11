import { Box, Text } from "ink"
import type { ReactNode } from "react"

import { clip, formatSeconds, formatThought, lastLines } from "../../../format.js"
import { exploreSummary, type Item } from "../../../state.js"
import { jobFailed, jobLabel, jobsCopy, jobTail } from "../../jobs.js"
import { Markdown, renderInline } from "../../markdown.js"
import { BAR, color, planStepColor } from "../../theme.js"
import { ToolCard } from "../../tools.js"

const THOUGHT_LINES = 12
const JOB_LINES = 6
const NOTICE_WIDTH = 120

function Banner() {
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text>
        <Text color={color.accent} bold>▌kvist</Text>
        <Text color={color.dim}>  local coding agent</Text>
      </Text>
      <Text color={color.faint}>/help for commands · esc esc to stop a run · ctrl+d to quit</Text>
    </Box>
  )
}

// The later calls of one model response hang off the first with a faint rail,
// so a batch reads as one group.
function InBatch({ index, children }: { index: number; children: ReactNode }) {
  if (index === 0) return <>{children}</>
  return (
    <Box>
      <Text color={color.faint}>┆ </Text>
      <Box flexDirection="column" flexShrink={1}>{children}</Box>
    </Box>
  )
}

function Step({ item }: { item: Extract<Item, { kind: "step" }> }) {
  const dot = planStepColor[item.planStep] ?? color.dim
  return (
    <Box flexDirection="column">
      <Text wrap="truncate-end">
        <Text color={dot}>● </Text>
        <Text bold>{item.action}</Text>
        {item.target ? <Text color={color.dim}>  {clip(item.target, 90)}</Text> : null}
      </Text>
    </Box>
  )
}

// A background job ended: how, and the last lines it printed.
function JobCard({ item }: { item: Extract<Item, { kind: "job" }> }) {
  const copy = jobsCopy(item.russian)
  const failed = jobFailed(item.status, item.exitCode)
  const tone = failed ? color.err : color.ok
  const { shown, earlier } = jobTail(item.output, JOB_LINES)
  return (
    <Box flexDirection="column">
      <Text wrap="truncate-end">
        <Text color={tone}>{failed ? "✗" : "✓"} </Text>
        <Text>{jobLabel(item)}</Text>
        <Text color={tone}> · {copy.ended(item.status, item.exitCode)}</Text>
        {item.seconds >= 1 ? <Text color={color.dim}> · {formatSeconds(item.seconds)}</Text> : null}
      </Text>
      <Box flexDirection="column" paddingLeft={2}>
        {earlier > 0 ? <Text color={color.faint}>{copy.earlier(earlier)}</Text> : null}
        {shown.length === 0 ? <Text color={color.faint}>{copy.noOutput}</Text> : null}
        {shown.map((line, i) => (
          <Text key={i} color={color.dim} wrap="truncate-end">{line || " "}</Text>
        ))}
      </Box>
    </Box>
  )
}

// A preview only: the model's whole note is in the session journal.
function JobNotice({ item }: { item: Extract<Item, { kind: "job_notice" }> }) {
  const lines = item.text.split("\n").filter((line) => line.trim())
  const preview = `${clip(lines[0] ?? "", NOTICE_WIDTH)}${lines.length > 1 ? " …" : ""}`
  return (
    <Text color={color.faint} wrap="truncate-end">› {jobsCopy(item.russian).told} {preview}</Text>
  )
}

function Plan({ item }: { item: Extract<Item, { kind: "plan" }> }) {
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={color.thought}>{item.russian ? "◇ предложенный план" : "◇ proposed plan"}</Text>
      <Box borderStyle={BAR} borderColor={color.thought} borderTop={false} borderBottom={false} borderRight={false} paddingLeft={1}>
        <Markdown text={item.markdown} />
      </Box>
    </Box>
  )
}

const toneColor = { info: color.dim, warn: color.warn, error: color.err, debug: color.faint } as const
const toneMark = { info: "·", warn: "!", error: "✗", debug: "›" } as const

export function ItemView({ item, showDebug }: { item: Item; showDebug: boolean }) {
  switch (item.kind) {
    case "banner":
      return <Banner />
    case "user":
      return (
        <Box marginTop={1} borderStyle={BAR} borderColor={color.accent} borderTop={false} borderBottom={false} borderRight={false} backgroundColor={color.panel} paddingX={1} paddingY={1}>
          <Text>{item.text}</Text>
        </Box>
      )
    case "plan":
      return <Plan item={item} />
    case "step":
      return <InBatch index={item.batchIndex}><Step item={item} /></InBatch>
    case "tool":
      return <InBatch index={item.batchIndex}><ToolCard item={item} /></InBatch>
    case "thought":
      return (
        <Box flexDirection="column" marginTop={1} borderStyle={BAR} borderColor={color.thought} borderTop={false} borderBottom={false} borderRight={false} paddingLeft={1}>
          <Text color={color.thought}>{formatThought(item.seconds)}</Text>
          {item.text ? <Text color={color.dim}>{renderInline(lastLines(item.text, THOUGHT_LINES))}</Text> : null}
        </Box>
      )
    case "explored":
      return <Text color={color.dim}>→ Explored – {exploreSummary(item.counts)}</Text>
    case "say":
      return <Text color={color.dim}>» {item.text}</Text>
    case "answer":
      return (
        <Box marginTop={1} marginBottom={1} paddingLeft={2}>
          <Markdown text={item.text} />
        </Box>
      )
    case "job":
      return <JobCard item={item} />
    case "job_notice":
      return <JobNotice item={item} />
    case "done":
      return null
    case "note":
      if (item.tone === "debug" && !showDebug) return null
      return (
        <Box>
          <Text color={toneColor[item.tone]}>{toneMark[item.tone]} </Text>
          <Box flexShrink={1}><Text color={toneColor[item.tone]}>{item.text}</Text></Box>
        </Box>
      )
  }
}
