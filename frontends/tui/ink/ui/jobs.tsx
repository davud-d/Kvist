// Background shell jobs: the copy both skins share and the strip of running jobs above the input.
import { Box, Text } from "ink"
import { useContext, useEffect, useState } from "react"

import { clip, formatSeconds, outputLines } from "../format.js"
import type { JobEnd, JobView } from "../state.js"
import { ViewContext } from "./context.js"
import { color, currentSkin, SPINNER } from "./theme.js"

const STRIP_JOBS = 3
const PURPOSE_WIDTH = 48
const LAST_LINE_WIDTH = 60
const EXPANDED_LINES = 5
const CLAUDE_FRAMES = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"]

export interface JobsCopy {
  background: string
  more: (count: number) => string
  noOutput: string
  earlier: (count: number) => string
  ended: (status: JobEnd, exitCode: number | null) => string
  told: string
}

const EN: JobsCopy = {
  background: "background",
  more: (count) => `+${count} more`,
  noOutput: "(no output)",
  earlier: (count) => `… +${count} earlier lines`,
  ended: (status, exitCode) => {
    if (status === "timed_out") return "timed out"
    if (status === "stopped") return "stopped"
    return `exit code ${exitCode ?? "?"}`
  },
  told: "model was told:",
}

const RU: JobsCopy = {
  background: "в фоне",
  more: (count) => `ещё ${count}`,
  noOutput: "(нет вывода)",
  earlier: (count) => `… ещё ${count} строк выше`,
  ended: (status, exitCode) => {
    if (status === "timed_out") return "время вышло"
    if (status === "stopped") return "остановлена"
    return `код выхода ${exitCode ?? "?"}`
  },
  told: "модель получила:",
}

export function jobsCopy(russian: boolean): JobsCopy {
  return russian ? RU : EN
}

// The words that say what a job is: the purpose the model gave, else the command itself.
export function jobLabel(job: { purpose: string; command: string[] }, width = PURPOSE_WIDTH): string {
  return clip(job.purpose || job.command.join(" ") || "job", width)
}

// The newest `count` lines of a job's output and how many older ones were left out.
export function jobTail(output: string, count: number): { shown: string[]; earlier: number } {
  const lines = outputLines(output)
  return { shown: lines.slice(-count), earlier: Math.max(0, lines.length - count) }
}

export function jobFailed(status: JobEnd, exitCode: number | null): boolean {
  return status !== "exited" || (exitCode ?? 0) !== 0
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

// One row per running job (three at most, then "+N more"): marker, purpose or command, how long
// it has run and the last line it printed. Ctrl+O in the fullscreen skin unfolds each job's tail.
export function JobStrip({ jobs, russian }: { jobs: readonly JobView[]; russian: boolean }) {
  const running = jobs.filter((job) => job.status === "running")
  const now = useNow(running.length > 0, 500)
  const { expanded } = useContext(ViewContext)
  if (running.length === 0) return null
  const copy = jobsCopy(russian)
  const claude = currentSkin() === "claude"
  const frames = claude ? CLAUDE_FRAMES : SPINNER
  const glyph = frames[Math.floor(now / 120) % frames.length] ?? "·"
  const lead = claude ? "⎿" : "›"
  const shown = running.slice(0, STRIP_JOBS)
  return (
    <Box flexDirection="column" paddingX={claude ? 1 : 0}>
      {shown.map((job) => {
        const last = outputLines(job.output).at(-1)?.trim() ?? ""
        return (
          <Box key={job.id} flexDirection="column">
            <Text wrap="truncate-end">
              <Text color={color.accent}>{glyph} </Text>
              <Text>{jobLabel(job)}</Text>
              <Text color={color.dim}> · {formatSeconds(Math.max(0, (now - job.startedAt) / 1000))}</Text>
              {last && !expanded ? <Text color={color.faint}> {lead} {clip(last, LAST_LINE_WIDTH)}</Text> : null}
            </Text>
            {expanded ? jobTail(job.output, EXPANDED_LINES).shown.map((line, index) => (
              <Text key={index} color={color.dim} wrap="truncate-end">  {lead} {line || " "}</Text>
            )) : null}
          </Box>
        )
      })}
      {running.length > shown.length ? <Text color={color.dim}>  {copy.more(running.length - shown.length)}</Text> : null}
    </Box>
  )
}
