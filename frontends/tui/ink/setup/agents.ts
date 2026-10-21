// The monogram each importable agent gets on the import step. They are Kvist's own marks, not the
// agents' logos (those are trademarks); `branding/agents/*.svg` draws the same letters and colours.
import type { RGB } from "./color.js"

export interface AgentMark {
  mark: string
  color: RGB
}

const FALLBACK: AgentMark = { mark: "··", color: [90, 100, 120] }

const MARKS: Readonly<Record<string, AgentMark>> = {
  "claude-code": { mark: "CC", color: [196, 106, 74] },
  codex: { mark: "CX", color: [46, 138, 110] },
  opencode: { mark: "OC", color: [64, 120, 200] },
  cursor: { mark: "CU", color: [110, 110, 130] },
  "qwen-code": { mark: "QW", color: [120, 84, 190] },
  continue: { mark: "CN", color: [200, 150, 50] },
  aider: { mark: "AI", color: [60, 160, 90] },
}

export function markOf(id: string): AgentMark {
  return MARKS[id] ?? FALLBACK
}
