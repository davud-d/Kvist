// Kvist: what the copied transcript rows read from OpenCode's session route (its `use()`); the
// route's anchors, grouping and queued-prompt machinery are not part of this.
import { createContext, useContext } from "solid-js"
import type { ModelInfo } from "@opencode/client"
import type { useConfig } from "../../config"
import type { ThinkingMode } from "../../context/thinking"

export const context = createContext<{
  /** Content width: terminal width minus the sidebar and padding. */
  width: number
  terminal: { width: number; height: number }
  sessionID: string
  thinkingMode: () => ThinkingMode
  markdownMode: () => "source" | "rendered"
  groupExploration: () => boolean
  diffWrapMode: () => "word" | "none"
  models: () => ModelInfo[]
  config: ReturnType<typeof useConfig>["data"]
}>()

export function use() {
  const ctx = useContext(context)
  if (!ctx) throw new Error("useContext must be used within a Session component")
  return ctx
}
