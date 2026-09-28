import { createContext } from "react"

import type { Pointer } from "../pointer.js"
import type { State } from "../state.js"

// What an item cannot read from its own data: the banner shows the workspace
// and the model that answered `hello`; the fullscreen skin listens to the mouse.
export interface ViewContextValue {
  workspace: string
  info: State["info"]
  effort: string
  pointer: Pointer | null
  // Ctrl+O: show every command with its output instead of "Ran N shell commands".
  expanded: boolean
}

export const ViewContext = createContext<ViewContextValue>({ workspace: "", info: { provider: "", model: "", role: "", session: "" }, effort: "", pointer: null, expanded: false })
