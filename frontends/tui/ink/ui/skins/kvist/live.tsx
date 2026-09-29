import { Box, Text } from "ink"
import { useWindowSize } from "../../window-size.js"
import { useEffect, useState } from "react"

import { clip, formatSeconds, lastLines } from "../../../format.js"
import { actionTarget, exploreSummary, lastInflight, type State } from "../../../state.js"
import { Markdown } from "../../markdown.js"
import { BAR, color, planStepColor, SPINNER } from "../../theme.js"

function useNow(active: boolean, every: number): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), every)
    return () => clearInterval(timer)
  }, [active, every])
  return now
}

function Thinking({ reasoning, spinner, seconds }: { reasoning: string; spinner: string; seconds: number }) {
  return (
    <Box flexDirection="column" marginTop={1} borderStyle={BAR} borderColor={color.thought} borderTop={false} borderBottom={false} borderRight={false} paddingLeft={1}>
      <Text color={color.thought}>
        {spinner} Thinking
        <Text color={color.faint}> · {formatSeconds(seconds)}</Text>
      </Text>
      {reasoning ? <Text color={color.dim} italic>{lastLines(reasoning, 3)}</Text> : null}
    </Box>
  )
}

// The live region is redrawn in place, so it must stay shorter than the
// viewport; the full text is committed to the scrollback when the stream ends.
export function Live({ state }: { state: State }) {
  const now = useNow(state.running || state.command !== null, 120)
  const { rows } = useWindowSize()
  const spinner = SPINNER[Math.floor(now / 120) % SPINNER.length] ?? ""
  const exploring = state.exploring ? <Text color={color.dim}>⋮ Exploring – {exploreSummary(state.exploring)}</Text> : null
  const inflight = lastInflight(state)
  const pendingAction = inflight ? (
    <Text wrap="truncate-end">
      <Text color={planStepColor[inflight.planStep] ?? color.dim}>{state.pending ? "●" : spinner} </Text>
      <Text bold>{inflight.action}</Text>
      <Text color={color.dim}>  {clip(actionTarget(inflight.args), 90)}</Text>
    </Text>
  ) : null
  if (state.command && !state.pending) {
    return (
      <Box flexDirection="column">
        <Text>
          <Text color={color.accent}>{spinner} </Text>
          <Text>Running /{state.command}…</Text>
        </Text>
      </Box>
    )
  }
  if (!state.running || state.pending) {
    return (
      <Box flexDirection="column">
        {exploring}
        {pendingAction}
      </Box>
    )
  }

  const status = state.status
  const elapsed = state.startedAt ? formatSeconds((now - state.startedAt) / 1000) : ""
  const live = state.live
  const budget = Math.max(4, rows - 14)

  return (
    <Box flexDirection="column">
      {exploring}
      {pendingAction}
      {live?.answer ? (
        <Box paddingLeft={2} marginBottom={1}>
          <Markdown text={lastLines(live.answer, budget)} />
        </Box>
      ) : null}
      {live?.thinking ? (
        <Thinking reasoning={live.reasoning} spinner={spinner} seconds={live.thinkingSince ? (now - live.thinkingSince) / 1000 : 0} />
      ) : (
        <Text>
          <Text color={status?.error ? color.err : color.accent}>{spinner} </Text>
          <Text>{status?.text || "Working"}</Text>
          {status?.planStep ? <Text color={planStepColor[status.planStep] ?? color.dim}> · {status.planStep}</Text> : null}
          {status?.step ? <Text color={color.dim}> · step {status.step}</Text> : null}
          <Text color={color.faint}> · {elapsed}</Text>
        </Text>
      )}
    </Box>
  )
}
