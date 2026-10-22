// Kvist: the part of OpenCode's client types (`@opencode/client`) that the copied transcript
// components read. They describe an assistant message and its parts; `screens/parts.ts` builds
// these from what the Kvist core reports.

export interface SessionMessageAssistantReasoning {
  type: "reasoning"
  id: string
  text: string
  time?: { created: number; completed?: number }
}

export interface SessionMessageAssistantText {
  type: "text"
  id: string
  text: string
}

export type ToolContent = { type: "text"; text: string } | { type: "file"; name?: string; uri: string }

export type ToolState =
  | { status: "streaming"; input: string | Record<string, unknown> }
  | { status: "running" | "completed"; input: Record<string, unknown>; metadata: Record<string, unknown>; content?: ToolContent[] }
  | { status: "error"; input: Record<string, unknown>; metadata: Record<string, unknown>; error: { message: string }; content?: ToolContent[] }

export interface SessionMessageAssistantTool {
  type: "tool"
  id: string
  name: string
  time: { created: number; completed?: number }
  state: ToolState
}

export interface SessionMessageAssistant {
  id: string
  type: "assistant"
  agent: string
  model: { providerID: string; id: string }
  time: { created: number; completed?: number }
  error?: { message: string; type?: string }
  retry?: unknown
}

export interface ModelInfo {
  providerID: string
  id: string
  name: string
}
