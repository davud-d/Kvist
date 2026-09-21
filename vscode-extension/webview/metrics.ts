import type { CoreEvent } from "../../src/protocol"

export interface TurnMetrics {
  thinking: number
  tokensPerSecond: number | null
}

export const NO_METRICS: TurnMetrics = { thinking: 0, tokensPerSecond: null }

// A call shorter than this yields a speed dominated by network latency, not generation.
const MIN_CALL_SECONDS = 0.3

// The core reports no per-call timing, so the speed is output tokens over the
// time since the previous call ended (or the turn began): an estimate that
// includes thinking and latency, and is shown as such.
export function trackMetrics(metrics: TurnMetrics, event: CoreEvent, callSeconds: number): TurnMetrics {
  switch (event.type) {
    case "reasoning_commit":
      return { ...metrics, thinking: metrics.thinking + event.duration_seconds }
    case "step":
      return { ...metrics, thinking: metrics.thinking + (event.reasoning_seconds || 0) }
    case "usage":
      if (event.output_tokens <= 0 || callSeconds < MIN_CALL_SECONDS) return metrics
      return { ...metrics, tokensPerSecond: event.output_tokens / callSeconds }
    default:
      return metrics
  }
}

export function shortSeconds(seconds: number): string {
  return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`
}
